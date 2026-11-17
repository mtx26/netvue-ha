package netvue

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
)

const (
	maxAccessUnitSize = maxBody - 1 - mediaHeaderSize
	maxH264Pixels     = 3840 * 2160
	maxH264Width      = 3840
	maxH264Height     = 2160
	// One extra macroblock permits the coded padding used at the 4K boundary.
	maxCodedH264Width  = maxH264Width + 16
	maxCodedH264Height = maxH264Height + 16
	maxCodedH264Pixels = maxCodedH264Width * maxCodedH264Height
	maxTimestampGap    = 10_000
	rtspTimeout        = 3 * time.Second
)

var (
	ErrVideoStartup  = errors.New("H.264 startup failed")
	ErrVideoNotReady = errors.New("H.264 stream is not ready")
	ErrVideoFatal    = errors.New("fatal video publisher error")
)

type timestampClock struct {
	hasPrevious bool
	previous    uint32
	elapsedRTP  uint32
}

func (clock *timestampClock) update(value uint32) uint32 {
	deltaMS := uint32(0)
	if clock.hasPrevious {
		delta := value - clock.previous
		if delta == 0 || delta > 0x80000000 || delta > maxTimestampGap {
			// Advance by the smallest source-time unit instead of guessing a frame rate.
			delta = 1
		}
		deltaMS = delta
	}
	clock.previous = value
	clock.hasPrevious = true
	clock.elapsedRTP += deltaMS * 90
	return clock.elapsedRTP
}

type videoPublisher struct {
	ctx                 context.Context
	url                 string
	client              *gortsplib.Client
	stopContext         func() bool
	media               *description.Media
	encoder             *rtph264.Encoder
	rtpBase             uint32
	startDeadline       time.Time
	sps                 []byte
	pps                 []byte
	clock               timestampClock
	started             bool
	published           uint64
	generationPublished uint64
	generation          uint64
}

func newVideoPublisher(ctx context.Context, outputURL string) (*videoPublisher, error) {
	if err := validateLocalRTSPURL(outputURL); err != nil {
		return nil, err
	}
	if err := contextError(ctx); err != nil {
		return nil, err
	}
	return &videoPublisher{ctx: ctx, url: outputURL}, nil
}

func (publisher *videoPublisher) Push(accessUnit []byte, timestamp uint32) error {
	if len(accessUnit) == 0 || len(accessUnit) > maxAccessUnitSize {
		return withReason("access unit size out of range", fmt.Errorf("%w: access unit size out of range", ErrProtocol))
	}
	var annexB h264.AnnexB
	if err := annexB.Unmarshal(accessUnit); err != nil {
		reason := "invalid Annex B access unit"
		switch {
		case errors.Is(err, h264.ErrAnnexBNoInitialDelimiter):
			reason = "missing Annex B initial delimiter"
		case errors.Is(err, h264.ErrAnnexBNoNALUs):
			reason = "Annex B access unit contains no NAL units"
		}
		return withReason(reason, fmt.Errorf("%w: parse Annex B access unit: %w", ErrProtocol, err))
	}

	hasIDR := false
	hasVCL := false
	hasPartitionA := false
	hasPartitionBC := false
	parametersChanged := false
	sps, pps := publisher.sps, publisher.pps
	var accessUnitSPS, accessUnitPPS []byte
	for _, nalu := range annexB {
		if len(nalu) == 0 || nalu[0]&0x80 != 0 {
			return withReason("invalid NAL unit", fmt.Errorf("%w: invalid H.264 NAL unit", ErrProtocol))
		}
		switch h264.NALUType(nalu[0] & 0x1f) {
		case h264.NALUTypeNonIDR:
			hasVCL = true
		case h264.NALUTypeDataPartitionA:
			hasVCL = true
			hasPartitionA = true
		case h264.NALUTypeDataPartitionB, h264.NALUTypeDataPartitionC:
			hasPartitionBC = true
		case h264.NALUTypeIDR:
			hasVCL = true
			hasIDR = true
		case h264.NALUTypeSPS:
			if accessUnitSPS != nil && !bytes.Equal(accessUnitSPS, nalu) {
				return withReason("conflicting SPS in one access unit", fmt.Errorf("%w: multiple SPS values in one access unit", ErrProtocol))
			}
			accessUnitSPS = nalu
			var parsed h264.SPS
			if err := parsed.Unmarshal(nalu); err != nil {
				return withReason("invalid SPS", fmt.Errorf("%w: parse H.264 SPS: %w", ErrProtocol, err))
			}
			if err := validateH264SPS(parsed); err != nil {
				return err
			}
			if !bytes.Equal(sps, nalu) {
				parametersChanged = sps != nil
				sps = nalu
			}
		case h264.NALUTypePPS:
			if accessUnitPPS != nil && !bytes.Equal(accessUnitPPS, nalu) {
				return withReason("conflicting PPS in one access unit", fmt.Errorf("%w: multiple PPS values in one access unit", ErrProtocol))
			}
			accessUnitPPS = nalu
			if len(nalu) < 2 {
				return withReason("truncated PPS", fmt.Errorf("%w: truncated H.264 PPS", ErrProtocol))
			}
			if !bytes.Equal(pps, nalu) {
				parametersChanged = parametersChanged || pps != nil
				pps = nalu
			}
		}
	}
	if hasPartitionBC && !hasPartitionA {
		return nil
	}
	if !bytes.Equal(publisher.sps, sps) {
		publisher.sps = append(publisher.sps[:0], sps...)
	}
	if !bytes.Equal(publisher.pps, pps) {
		publisher.pps = append(publisher.pps[:0], pps...)
	}
	if parametersChanged && publisher.started {
		publisher.stop()
	}
	if !hasVCL {
		return nil
	}
	elapsedRTP := publisher.clock.update(timestamp)
	if !publisher.started {
		if !hasIDR {
			return nil
		}
		if len(publisher.sps) == 0 || len(publisher.pps) == 0 {
			return withReason("IDR arrived before SPS/PPS", fmt.Errorf("%w: IDR arrived before SPS/PPS", ErrVideoNotReady))
		}
		if err := publisher.start(); err != nil {
			return atStage("RTSP publication", err)
		}
		annexB = prependParameters(annexB, publisher.sps, publisher.pps)
	}

	packets, err := publisher.encoder.Encode(annexB)
	if err != nil {
		return fmt.Errorf("packetize H.264 access unit: %w", err)
	}
	rtpTimestamp := publisher.rtpBase + elapsedRTP
	for _, packet := range packets {
		packet.Timestamp = rtpTimestamp
		if err := publisher.client.WritePacketRTP(publisher.media, packet); err != nil {
			return atStage("RTSP publication", fmt.Errorf("write RTSP RTP packet: %w", err))
		}
	}
	publisher.published++
	publisher.generationPublished++
	return nil
}

func (publisher *videoPublisher) SetStartupDeadline(deadline time.Time) {
	publisher.startDeadline = deadline
}

func validateH264SPS(sps h264.SPS) error {
	// SPS dimensions are macroblock counts; use uint64 before cropping to avoid overflow.
	widthInMbs := uint64(sps.PicWidthInMbsMinus1) + 1
	heightInMapUnits := uint64(sps.PicHeightInMapUnitsMinus1) + 1
	frameHeightFactor := uint64(1)
	if !sps.FrameMbsOnlyFlag {
		frameHeightFactor = 2
	}
	codedWidth := widthInMbs * 16
	codedHeight := heightInMapUnits * 16 * frameHeightFactor
	if codedWidth > maxCodedH264Width || codedHeight > maxCodedH264Height ||
		codedWidth*codedHeight > maxCodedH264Pixels {
		return withReason("coded dimensions exceed configured limit", fmt.Errorf("%w: coded H.264 dimensions exceed configured limit", ErrProtocol))
	}

	chromaArrayType := sps.ChromaFormatIdc
	if sps.SeparateColourPlaneFlag {
		chromaArrayType = 0
	}
	cropUnitX, cropUnitY := uint64(1), frameHeightFactor
	switch chromaArrayType {
	case 0:
	case 1:
		cropUnitX, cropUnitY = 2, 2*frameHeightFactor
	case 2:
		cropUnitX, cropUnitY = 2, frameHeightFactor
	case 3:
		cropUnitX, cropUnitY = 1, frameHeightFactor
	default:
		return withReason("unsupported chroma format", fmt.Errorf("%w: unsupported H.264 chroma format", ErrProtocol))
	}
	if sps.FrameCropping != nil {
		cropWidth := (uint64(sps.FrameCropping.LeftOffset) +
			uint64(sps.FrameCropping.RightOffset)) * cropUnitX
		cropHeight := (uint64(sps.FrameCropping.TopOffset) +
			uint64(sps.FrameCropping.BottomOffset)) * cropUnitY
		if cropWidth >= codedWidth || cropHeight >= codedHeight {
			return withReason("invalid frame cropping", fmt.Errorf("%w: invalid H.264 frame cropping", ErrProtocol))
		}
		codedWidth -= cropWidth
		codedHeight -= cropHeight
	}
	if codedWidth > maxH264Width || codedHeight > maxH264Height ||
		codedWidth*codedHeight > maxH264Pixels {
		return withReason("displayed dimensions exceed configured limit", fmt.Errorf("%w: displayed H.264 dimensions exceed configured limit", ErrProtocol))
	}
	return nil
}

func (publisher *videoPublisher) start() error {
	if err := contextError(publisher.ctx); err != nil {
		return err
	}
	startCtx := publisher.ctx
	var cancelStartup context.CancelFunc
	hasStartupDeadline := !publisher.startDeadline.IsZero()
	if hasStartupDeadline {
		startCtx, cancelStartup = context.WithDeadline(publisher.ctx, publisher.startDeadline)
		defer cancelStartup()
	}
	startupContextError := func() error {
		err := contextError(startCtx)
		if err != nil && publisher.ctx.Err() == nil &&
			errors.Is(err, context.DeadlineExceeded) && !publisher.startDeadline.IsZero() {
			return fmt.Errorf("%w: RTSP setup exceeded video startup deadline: %w", ErrVideoStartup, err)
		}
		return err
	}
	h264Format := &format.H264{
		PayloadTyp:        96,
		SPS:               append([]byte(nil), publisher.sps...),
		PPS:               append([]byte(nil), publisher.pps...),
		PacketizationMode: 1,
	}
	encoder, err := h264Format.CreateEncoder()
	if err != nil {
		return fmt.Errorf("%w: create H.264 RTP encoder: %w", ErrVideoFatal, err)
	}
	media := &description.Media{
		Type:    description.MediaTypeVideo,
		Formats: []format.Format{h264Format},
	}
	var initialTimestamp [4]byte
	if _, err := rand.Read(initialTimestamp[:]); err != nil {
		return fmt.Errorf("%w: generate RTP timestamp base: %w", ErrVideoFatal, err)
	}
	transport := gortsplib.ProtocolTCP
	client := &gortsplib.Client{
		ReadTimeout:  rtspTimeout,
		WriteTimeout: rtspTimeout,
		Protocol:     &transport,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			dialCtx, cancel := context.WithTimeout(startCtx, rtspTimeout)
			stop := context.AfterFunc(ctx, cancel)
			defer func() {
				stop()
				cancel()
			}()
			return (&net.Dialer{}).DialContext(dialCtx, network, address)
		},
	}
	u, err := base.ParseURL(publisher.url)
	if err != nil {
		return fmt.Errorf("%w: parse RTSP publication URL: %w", ErrVideoFatal, err)
	}
	client.Scheme = u.Scheme
	client.Host = u.Host
	if err := client.Start(); err != nil {
		return fmt.Errorf("%w: start RTSP client: %w", ErrVideoFatal, err)
	}
	// Closing the gortsplib client interrupts any request blocked on RTSP I/O.
	stopOnCancel := context.AfterFunc(publisher.ctx, func() {
		closeRTSPClient(client)
	})
	var stopOnStartupDeadline func() bool
	if hasStartupDeadline {
		stopOnStartupDeadline = context.AfterFunc(startCtx, func() {
			closeRTSPClient(client)
		})
	}
	published := false
	defer func() {
		if !published {
			// Close the socket first so an in-flight RTSP operation cannot stall cleanup.
			closeRTSPClient(client)
			stopOnCancel()
			if stopOnStartupDeadline != nil {
				stopOnStartupDeadline()
			}
		}
	}()
	desc := &description.Session{Medias: []*description.Media{media}}
	// Keep each RTSP request interruptible by the session context.
	response, err := client.Announce(u, desc)
	if err != nil {
		if ctxErr := startupContextError(); ctxErr != nil {
			return ctxErr
		}
		return rtspStartupError("announce RTSP publication", response, err)
	}
	if err := client.SetupAll(u, desc.Medias); err != nil {
		if ctxErr := startupContextError(); ctxErr != nil {
			return ctxErr
		}
		return rtspStartupError("set up RTSP publication", nil, err)
	}
	response, err = client.Record()
	if err != nil {
		if ctxErr := startupContextError(); ctxErr != nil {
			return ctxErr
		}
		return rtspStartupError("start RTSP recording", response, err)
	}
	if err := startupContextError(); err != nil {
		return err
	}
	if stopOnStartupDeadline != nil {
		if !stopOnStartupDeadline() {
			if err := startupContextError(); err != nil {
				return err
			}
		}
		cancelStartup()
	}
	if err := contextError(publisher.ctx); err != nil {
		return err
	}
	publisher.client = client
	publisher.stopContext = stopOnCancel
	publisher.media = media
	publisher.encoder = encoder
	publisher.rtpBase = binary.BigEndian.Uint32(initialTimestamp[:])
	publisher.generation++
	publisher.started = true
	publisher.generationPublished = 0
	publisher.startDeadline = time.Time{}
	published = true
	return nil
}

func closeRTSPClient(client *gortsplib.Client) {
	if conn := client.NetConn(); conn != nil {
		_ = conn.Close()
	}
	client.Close()
}

func rtspStartupError(operation string, response *base.Response, err error) error {
	var status base.StatusCode
	var statusErr liberrors.ErrClientBadStatusCode
	if errors.As(err, &statusErr) {
		status = statusErr.Code
	} else if response != nil {
		status = response.StatusCode
	}
	if status == base.StatusServiceUnavailable {
		return fmt.Errorf("%w: %s (RTSP status %d): %w", ErrVideoStartup, operation, status, err)
	}
	if status >= base.StatusBadRequest {
		return fmt.Errorf("%w: %s (RTSP status %d): %w", ErrVideoFatal, operation, status, err)
	}
	var networkError net.Error
	if errors.As(err, &networkError) || errors.Is(err, context.DeadlineExceeded) {
		return fmt.Errorf("%w: %s: %w", ErrVideoStartup, operation, err)
	}
	return fmt.Errorf("%w: %s: %w", ErrVideoFatal, operation, err)
}

func (publisher *videoPublisher) stop() {
	if publisher.client != nil {
		publisher.client.Close()
	}
	if publisher.stopContext != nil {
		// Keep cancellation able to interrupt Close until the client is stopped.
		publisher.stopContext()
		publisher.stopContext = nil
	}
	publisher.client = nil
	publisher.media = nil
	publisher.encoder = nil
	publisher.started = false
	publisher.generationPublished = 0
}

func (publisher *videoPublisher) Close() {
	publisher.stop()
}

func (publisher *videoPublisher) PublishedFrames() uint64 {
	return publisher.published
}

func (publisher *videoPublisher) CurrentPublicationFrames() uint64 {
	return publisher.generationPublished
}

func (publisher *videoPublisher) Generation() uint64 {
	return publisher.generation
}

func validateLocalRTSPURL(value string) error {
	parsed, err := url.Parse(value)
	if err != nil {
		return fmt.Errorf("invalid RTSP output URL: %w", err)
	}
	host := net.ParseIP(parsed.Hostname())
	if parsed.Scheme != "rtsp" || host == nil || !host.IsLoopback() ||
		parsed.User != nil || parsed.Port() == "" || parsed.Path == "" ||
		parsed.Path == "/" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return errors.New("RTSP output must be a loopback URL with an explicit port and path")
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port < 1 || port > 65535 {
		return errors.New("RTSP output port is invalid")
	}
	return nil
}

func prependParameters(accessUnit h264.AnnexB, sps, pps []byte) h264.AnnexB {
	hasSPS, hasPPS := false, false
	for _, nalu := range accessUnit {
		hasSPS = hasSPS || bytes.Equal(nalu, sps)
		hasPPS = hasPPS || bytes.Equal(nalu, pps)
	}
	out := make(h264.AnnexB, 0, len(accessUnit)+2)
	if !hasSPS {
		out = append(out, sps)
	}
	if !hasPPS {
		out = append(out, pps)
	}
	return append(out, accessUnit...)
}

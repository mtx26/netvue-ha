package e2e

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/pion/rtp"
)

// AVOffset reads the published RTSP stream with an independent client and
// returns NTP(video) - NTP(audio) from the latest observed packet on each track
// after minimum sample counts. The RTCP-derived observations are not guaranteed
// to have arrived together.
func AVOffset(ctx context.Context, rawURL string) (time.Duration, error) {
	u, err := base.ParseURL(rawURL)
	if err != nil {
		return 0, err
	}
	client := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: ptr(gortsplib.ProtocolTCP)}
	if err := client.Start(); err != nil {
		return 0, err
	}
	defer client.Close()
	desc, _, err := client.Describe(u)
	if err != nil {
		return 0, err
	}
	if len(desc.Medias) != 2 {
		return 0, fmt.Errorf("stream has %d tracks, want video and audio", len(desc.Medias))
	}
	if err := client.SetupAll(desc.BaseURL, desc.Medias); err != nil {
		return 0, err
	}
	type seen struct {
		ntp   time.Time
		count int
	}
	var mu sync.Mutex
	latest := map[description.MediaType]*seen{description.MediaTypeVideo: {}, description.MediaTypeAudio: {}}
	done := make(chan struct{})
	var once sync.Once
	client.OnPacketRTPAny(func(medi *description.Media, _ format.Format, pkt *rtp.Packet) {
		ntp, ok := client.PacketNTP(medi, pkt)
		if !ok {
			return
		}
		mu.Lock()
		defer mu.Unlock()
		s := latest[medi.Type]
		s.ntp, s.count = ntp, s.count+1
		if latest[description.MediaTypeVideo].count >= 10 && latest[description.MediaTypeAudio].count >= 5 {
			once.Do(func() { close(done) })
		}
	})
	if _, err := client.Play(nil); err != nil {
		return 0, err
	}
	select {
	case <-done:
	case <-ctx.Done():
		return 0, fmt.Errorf("no RTCP-synchronised audio and video packets: %w", ctx.Err())
	case err := <-waitErr(client):
		return 0, fmt.Errorf("RTSP reader stopped: %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	return latest[description.MediaTypeVideo].ntp.Sub(latest[description.MediaTypeAudio].ntp), nil
}

func waitErr(c *gortsplib.Client) <-chan error {
	out := make(chan error, 1)
	go func() { out <- c.Wait() }()
	return out
}

func ptr[T any](v T) *T { return &v }

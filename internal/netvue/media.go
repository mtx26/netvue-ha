package netvue

import (
	"encoding/binary"
	"errors"
	"fmt"
)

const (
	videoFrameType  = 1
	otherFrameType  = 2
	mediaHeaderSize = 5
)

var ErrMediaFrame = errors.New("invalid Netvue media frame")

type videoFrame struct {
	timestamp  uint32
	accessUnit []byte
}

func parseMediaFrame(body []byte) (videoFrame, bool, error) {
	if len(body) < mediaHeaderSize+1 {
		return videoFrame{}, false, withReason("truncated frame header or empty payload", fmt.Errorf("%w: truncated frame header", ErrMediaFrame))
	}
	switch body[0] >> 4 {
	case videoFrameType:
		return videoFrame{
			timestamp:  binary.LittleEndian.Uint32(body[1:mediaHeaderSize]),
			accessUnit: body[mediaHeaderSize:],
		}, true, nil
	case otherFrameType:
		return videoFrame{}, false, nil
	default:
		return videoFrame{}, false, withReason("unsupported frame type", fmt.Errorf("%w: unsupported frame type", ErrMediaFrame))
	}
}

package manager

import (
	"context"
	"errors"
	"testing"

	"github.com/mtx26/netvue-ha/internal/netvue"
)

func TestTalkbackCameraIsolationAndLifecycle(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	for _, camera := range []struct{ host, name string }{
		{"192.168.10.20", "one"}, {"192.168.10.21", "two"},
	} {
		if _, _, err := manager.AddCamera(camera.host, camera.name); err != nil {
			t.Fatal(err)
		}
	}
	if manager.cameras["192.168.10.20"].talkback == manager.cameras["192.168.10.21"].talkback {
		t.Fatal("cameras share a talkback controller")
	}
	if _, err := manager.OpenTalkback(context.Background(), "192.168.10.20"); !errors.Is(err, netvue.ErrTalkUnavailable) {
		t.Fatalf("without live audio: %v", err)
	}
	if _, err := manager.OpenTalkback(context.Background(), "192.168.10.22"); !errors.Is(err, ErrCameraNotFound) {
		t.Fatalf("unknown camera: %v", err)
	}
	if _, err := manager.StopCamera(context.Background(), "192.168.10.20"); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.OpenTalkback(context.Background(), "192.168.10.20"); !errors.Is(err, netvue.ErrTalkUnavailable) {
		t.Fatalf("stopped camera: %v", err)
	}
	if _, err := manager.OpenTalkback(context.Background(), "192.168.10.21"); !errors.Is(err, netvue.ErrTalkUnavailable) {
		t.Fatalf("other camera: %v", err)
	}
}

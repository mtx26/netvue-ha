package manager

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestAIMetadataStorePersistsAndQueriesByTimeAndStream(t *testing.T) {
	dir := t.TempDir()
	store, err := newAIMetadataStoreWithLimits(dir, 10, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	event := testAIMetadata("event-1", "camera-a", float64(now+1))
	event.Boxes[0].X1 = 12.25
	if stored, err := store.add(event); err != nil || !stored {
		t.Fatalf("add event = %t, %v", stored, err)
	}
	otherStream := testAIMetadata(event.ID, "camera-b", event.Timestamp)
	if stored, err := store.add(otherStream); err != nil || !stored {
		t.Fatalf("add same id to another stream = %t, %v", stored, err)
	}
	if stored, err := store.add(event); err != nil || stored {
		t.Fatalf("duplicate add = %t, %v", stored, err)
	}

	recovered, err := newAIMetadataStoreWithLimits(dir, 10, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	start := time.Unix(int64(now), 0)
	got := recovered.between("camera-a", start, start.Add(2*time.Second))
	if len(got) != 1 {
		t.Fatalf("query returned %d events, want 1", len(got))
	}
	if got[0].Boxes[0].X1 != 12.25 || got[0].FrameWidth != event.FrameWidth ||
		got[0].TrackID != event.TrackID {
		t.Fatalf("recovered metadata = %+v", got[0])
	}
	if other := recovered.between("camera-b", start, start.Add(2*time.Second)); len(other) != 1 {
		t.Fatalf("query for another stream returned %d events, want 1", len(other))
	}
}

func TestAIMetadataStoreBoundsHistoryAndRecoversTornTail(t *testing.T) {
	dir := t.TempDir()
	store, err := newAIMetadataStoreWithLimits(dir, 2, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	base := float64(time.Now().Unix())
	for index := 1; index <= 3; index++ {
		if _, err := store.add(testAIMetadata(string(rune('a'+index)), "camera", base+float64(index))); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(dir, aiMetadataFilename)
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString(`{"id":"incomplete"`); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}

	recovered, err := newAIMetadataStoreWithLimits(dir, 2, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	got := recovered.between("camera", time.Time{}, time.Time{})
	if len(got) != 2 || got[0].Timestamp != base+2 || got[1].Timestamp != base+3 {
		t.Fatalf("bounded history = %+v", got)
	}
	if _, err := recovered.add(testAIMetadata("e", "camera", base+4)); err != nil {
		t.Fatal(err)
	}
	recovered, err = newAIMetadataStoreWithLimits(dir, 2, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	got = recovered.between("camera", time.Time{}, time.Time{})
	if len(got) != 2 || got[0].Timestamp != base+3 || got[1].Timestamp != base+4 {
		t.Fatalf("recovered compacted history = %+v", got)
	}
}

func TestAIMetadataEndpointPersistsAndAssociatesEventsWithRecording(t *testing.T) {
	segmentStartTime := time.Now().UTC().Truncate(time.Second)
	segmentStart := segmentStartTime.Format(time.RFC3339)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/v3/recordings/get/camera" {
			_, _ = w.Write([]byte(`{"segments":[{"start":"` + segmentStart + `","duration":60}]}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer control.Close()

	dir := t.TempDir()
	store, err := newAIMetadataStoreWithLimits(dir, 10, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	manager := &Manager{
		cameras: map[string]*cameraSession{
			"192.168.10.2": {Camera: Camera{
				Host: "192.168.10.2", StreamName: "camera",
			}},
		},
		recordings: &recordingManager{
			ctx:        context.Background(),
			dir:        dir,
			controlURL: control.URL,
			client:     control.Client(),
		},
		aiMetadata: store,
	}
	event := testAIMetadata(
		"event", "camera", float64(segmentStartTime.Add(5*time.Second).Unix()),
	)
	body, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	handler := manager.Handler()
	ingest := httptest.NewRecorder()
	handler.ServeHTTP(ingest, httptest.NewRequest(
		http.MethodPost,
		"/api/v1/cameras/192.168.10.2/ai/metadata",
		bytes.NewReader(body),
	))
	if ingest.Code != http.StatusCreated {
		t.Fatalf("ingest status = %d, body %s", ingest.Code, ingest.Body)
	}

	segments, err := manager.recordings.list("camera", time.Time{}, time.Time{})
	if err != nil || len(segments) != 1 {
		t.Fatalf("recording segments = %+v, %v", segments, err)
	}
	query := httptest.NewRecorder()
	handler.ServeHTTP(query, httptest.NewRequest(
		http.MethodGet,
		"/api/v1/cameras/192.168.10.2/recordings/"+segments[0].ID+"/metadata",
		nil,
	))
	if query.Code != http.StatusOK {
		t.Fatalf("metadata status = %d, body %s", query.Code, query.Body)
	}
	var response struct {
		Events []AIDetectionMetadata `json:"events"`
	}
	if err := json.Unmarshal(query.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if len(response.Events) != 1 || response.Events[0].Boxes[0].X1 != event.Boxes[0].X1 {
		t.Fatalf("recording metadata response = %+v", response.Events)
	}

	rangeStart := segmentStartTime.Add(4 * time.Second).Format(time.RFC3339)
	rangeEnd := segmentStartTime.Add(6 * time.Second).Format(time.RFC3339)
	rangeResponse := httptest.NewRecorder()
	handler.ServeHTTP(rangeResponse, httptest.NewRequest(
		http.MethodGet,
		"/api/v1/cameras/192.168.10.2/ai/metadata?start="+rangeStart+"&end="+rangeEnd,
		nil,
	))
	if rangeResponse.Code != http.StatusOK {
		t.Fatalf("range metadata status = %d, body %s", rangeResponse.Code, rangeResponse.Body)
	}
	if err := json.Unmarshal(rangeResponse.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if len(response.Events) != 1 || response.Events[0].TrackID != event.TrackID {
		t.Fatalf("range metadata response = %+v", response.Events)
	}
}

func testAIMetadata(id, stream string, timestamp float64) AIDetectionMetadata {
	return AIDetectionMetadata{
		ID:          id,
		Timestamp:   timestamp,
		StreamName:  stream,
		Category:    "person",
		Confidence:  0.9,
		TrackID:     7,
		FrameWidth:  1920,
		FrameHeight: 1080,
		Boxes: []AIDetectionBox{{
			X1: 10, Y1: 20, X2: 100, Y2: 200,
		}},
	}
}

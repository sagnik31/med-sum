package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"testing"
	"time"
)

func TestShutdownLetsARequestInFlightFinish(t *testing.T) {
	started := make(chan struct{})
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		time.Sleep(300 * time.Millisecond) // an upload still being written
		_, _ = w.Write([]byte("stored"))
	})}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan error, 1)
	go func() { served <- serve(ctx, srv, l, 5*time.Second) }()

	answer := make(chan string, 1)
	go func() {
		resp, err := http.Get("http://" + l.Addr().String() + "/")
		if err != nil {
			answer <- "error: " + err.Error()
			return
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		answer <- string(body)
	}()
	<-started
	cancel() // SIGTERM while the request runs

	if got := <-answer; got != "stored" {
		t.Errorf("the request in flight got %q", got)
	}
	if err := <-served; err != nil {
		t.Errorf("serve = %v", err)
	}
	if _, err := net.DialTimeout("tcp", l.Addr().String(), time.Second); err == nil {
		t.Error("still accepting connections after shutdown")
	}
}

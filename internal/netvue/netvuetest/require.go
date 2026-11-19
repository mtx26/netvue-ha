// Package netvuetest provides one deterministic Netvue camera simulator for
// Go tests and for the container-based end-to-end scenario.
//
// The simulator speaks the LAN protocol documented in docs/netvue-protocol.md
// with its own framing code. It deliberately does not import the production
// package: a shared bug in both sides could otherwise hide itself.
package netvuetest

import (
	"fmt"
	"os"
	"os/exec"
	"sync/atomic"
)

// RequireEnv makes unavailable integration prerequisites a failure instead of
// a skip. CI sets it to "1" for the jobs that must execute the scenarios.
const RequireEnv = "NETVUE_REQUIRE_INTEGRATION"

// TB is the subset of testing.TB used here. It keeps package testing out of
// the simulator command.
type TB interface {
	Helper()
	Logf(format string, args ...any)
	Fatalf(format string, args ...any)
	Skipf(format string, args ...any)
	Cleanup(func())
}

// Required reports whether skipped integration tests must fail.
func Required() bool { return os.Getenv(RequireEnv) == "1" }

// RequireIntegration skips the test, or fails it when NETVUE_REQUIRE_INTEGRATION=1,
// because an external prerequisite is missing. Never call it for a defect.
func RequireIntegration(t TB, format string, args ...any) {
	t.Helper()
	reason := fmt.Sprintf(format, args...)
	if Required() {
		t.Fatalf("%s=1 but an integration prerequisite is unavailable: %s", RequireEnv, reason)
		return
	}
	t.Skipf("integration prerequisite unavailable (%s=1 makes this fatal): %s", RequireEnv, reason)
}

// Executable resolves a tool from an environment variable, then from PATH.
func Executable(t TB, envName, name string) string {
	t.Helper()
	candidate := os.Getenv(envName)
	if candidate == "" {
		candidate = name
	}
	path, err := exec.LookPath(candidate)
	if err != nil {
		RequireIntegration(t, "%s is unavailable (set %s or add it to PATH): %v", name, envName, err)
		return ""
	}
	return path
}

var hostCounter atomic.Uint32

// AllocHost returns a fresh loopback address 127.<group>.<n>.<m> for one camera.
// Discovery uses the fixed UDP port 36868, so every simulated camera needs its
// own address. Packages running in parallel must use different groups.
// The whole 127/8 block is loopback on Linux and Windows.
func AllocHost(group byte) string {
	n := hostCounter.Add(1)
	return fmt.Sprintf("127.%d.%d.%d", group, n/250, 2+n%250)
}

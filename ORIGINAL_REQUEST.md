# Original User Request

## Initial Request — 2026-08-14T20:04:58+07:00

Comprehensive audit, load capacity benchmarking, fault tolerance and chaos resilience evaluation, security hardening, and source-code optimization for AgyMobile.

Working directory: e:/Project/Agy_desktop_mobie
Integrity mode: development

## Requirements

### R1. Concurrency & High-Load Performance Evaluation
Conduct high-concurrency stress testing on WebSocket channels (/ws/session, /ws/terminal, /ws/system), transcript log tailing with large JSONL data streams, and rapid telemetry polling. Identify any thread-blocking operations, event loop lag, or memory bloat, and optimize for sustained high throughput and low latency.

### R2. Fault Tolerance, Chaos & Edge-Case Resilience
Evaluate and harden system behavior under adverse conditions including unexpected network disconnects and reconnects, malformed/corrupted transcript streams, subprocess termination/hangs, concurrent file access/locking under Windows OS, and out-of-order WebSocket frames.

### R3. Security, Sandbox & Authentication Verification
Verify authentication flows (PIN/JWT rate limiting, token expiration), path traversal prevention across the workspace sandbox, and safe subprocess execution boundaries. Remediate any identified privilege escalation or boundary escape vulnerabilities.

### R4. Mobile PWA & Terminal UX Stability
Verify frontend PWA robustness, offline/reconnection handling, VT100 terminal rendering performance with heavy ANSI outputs, touch gesture fluidity, and dynamic viewport handling on mobile screen sizes.

### R5. Comprehensive Audit Report & Automated Test Verification
Deliver a detailed audit & benchmark report covering performance metrics (latency, RPS, memory), vulnerability findings, applied patches, and automated tests validating all fixes and stress benchmarks.

## Acceptance Criteria

### Automated Testing & Benchmark Passing
- [ ] All existing and newly created Pytest test suites (including adversarial and stress test suites) pass with 100% success rate.
- [ ] Load and concurrency benchmarks demonstrate stable memory usage without unbounded growth or event loop starvation.
- [ ] Chaos and failure recovery tests confirm graceful degradation and successful state recovery without server crash.

### Code Quality & Security Hardening
- [ ] Sandbox boundaries strictly prevent unauthorized file system traversal outside configured workspaces.
- [ ] WebSockets and subprocess handlers cleanly release resources and background tasks upon disconnect or error.
- [ ] An audit summary document detailing test results, performance numbers, and improvements is generated in the workspace.

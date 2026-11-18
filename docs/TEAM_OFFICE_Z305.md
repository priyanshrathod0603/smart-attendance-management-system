# Team Office Z305 integration status

## What is verified

The configured terminal is identified as Team Office Z305, but its exact suffix/variant and firmware have not been verified from a label or vendor-issued device report.

The manufacturer's public [download portal](https://downloadpage.etimeoffice.com/) lists **SDK DOWNLOAD** (three files) and **Manual / CATALOG DOCUMENTS** (four files). The portal requires a download request and verification; it does not publish individual filenames, SDK versions, a support matrix, API reference, or Z305 protocol specification. There is therefore no publicly verifiable SDK filename/version to configure here. Request the TeamOffice SDK and Z305 compatibility confirmation through that portal, or contact Team Office at +91-9924502024.

The configured endpoint uses port 5005, but the vendor's public materials do not name that port or identify the wire protocol behind it. A TCP-reachable result means only that a TCP listener accepted a connection at the time of the check; port 5005 is not verified as a Z305 command/SDK port. A non-command TCP probe from this execution environment currently fails, so this runtime cannot refresh the earlier reachability observation. No application protocol commands were sent.

The same portal offers a **Team Office Windows Software** category (three packages) and a **SQL Server and Studio** category. Public pages do not reveal the package names or establish that the software uses SQL Server, nor expose a supported database schema/API/export. The vendor's [eTimeOffice pricing page](https://www.etimeoffice.com/NewHome/PricingPage) advertises third-party API integration for its hosted attendance product, but does not publish the API specification or establish that it can receive this Z305 model's punches. So no direct database/API connector can be implemented safely from public information.

The manufacturer's [Z305W page](https://teamoffice.in/products/z305w) identifies the model as Z305W and lists USB, TCP/IP, and Wi-Fi as connection methods. It does not specify a TCP port, wire protocol, API, SDK functions, user-enrollment workflow, or attendance-log retrieval mechanism. The separate [Z305CB page](https://teamoffice.in/products/z305cb-fingerprint-attendance-device) describes fingerprint/card/PIN and cloud features but does not document the interface protocol. Neither public product page confirms ZKTeco compatibility. The [Z305 product catalog](https://timeoffice.in/images/PDFUpload/Product-PDFUpload-2918180703582958-Z305.pdf) lists TCP/IP and **Data Push** for the Z305 family. This supports that a push-capable variant exists, but does not identify this terminal's suffix/firmware or define a push protocol/payload. Do not point it at the AttendanceOS webhook until Team Office confirms its format and transport or supplies a translating gateway/SDK.

Therefore, AttendanceOS does **not** assume ZKTeco/pyzk compatibility, port 4370, ADMS, HTTP push support, or any undocumented Team Office SDK function.

## Current implementation boundary

- The real adapter can test a user-configured TCP endpoint only. The device page now reports Network Connected, Protocol Verified, Device Authenticated, Enrollment Supported, and Attendance Sync Working separately. For this Z305 adapter, only the first can become true from a TCP connect; the others remain false until a documented implementation establishes them.
- The endpoint is configured on port 5005; that port is not confirmed by Team Office as a Z305 protocol port. Do not use a successful socket check as proof of a device session.
- Real user registration, fingerprint capture/enrollment, template upload, attendance-log polling, event subscription, and automatic missed-log recovery are not implemented. Those operations return an explicit unsupported/unverified result rather than simulated success.
- `MockFingerprintAdapter` can simulate enrollment, connection recovery, and queued logs for tests. Mock results carry `simulated: true` and do not verify physical hardware.
- `POST /api/devices/biomax/event` accepts an AttendanceOS-normalized JSON contract. It is not evidence that Z305 firmware can send this contract.
- The normalized endpoint authenticates device requests with a per-device bearer token, validates registered active devices and explicit teacher/device mappings, converts timestamps using `BIOMAX_DEVICE_TIMEZONE`, and stores idempotency receipts before attendance processing.
- The biometric attendance schema maps device user IDs to `Teacher` records. Teacher creation also creates/links an HR `Employee` using the staff code, but standalone non-teaching employees are not yet attendance targets. Configure the exact device user ID as a string on the Teacher Mapping page; do not rely on numeric conversion or an assumed employee-code match.
- A separate attendance-file fallback is implemented in `biometrics/attendance_file_import.py` with `scripts/watch_z305_exports.py`. It imports CSV, TSV, TXT, and XLSX exports using recognized employee-ID and timestamp columns, resolves explicit teacher mappings, stores durable event receipts for deduplication, and processes punches through AttendanceOS's common attendance engine. Z305 product material lists USB Memory Disk connectivity but does not provide its matching manual or verify that the reported Z305 firmware exports attendance files. Use this only if the device's own menu/manual or official software can produce a supported file. It allows recorded device punches through even when the local enrollment flag is false; it never changes that flag or claims enrollment occurred. `.xlsx` support uses `openpyxl`; legacy `.xls` is not supported.
- Administrators can upload a one-time export from **Devices → Fingerprint Device → Import Device Attendance Export**. The upload endpoint has a 10 MB limit, enforces the existing `devices.manage` permission and CSRF protection, and returns imported/duplicate/rejected row counts. The same parser and persistence path power the folder watcher.
- For folder synchronization, copy each device-generated attendance file into a watched folder and run `venv\Scripts\python.exe scripts\watch_z305_exports.py --folder D:\Z305\exports --device-id Z305_MAIN --school-id 1`. Optional flags are `--timezone Asia/Kolkata`, `--interval 10`, and `--once`. Re-scanning a file or restarting the watcher is safe: identical device/user/timestamp punches are deduplicated in PostgreSQL. Files with unknown/unmapped users remain retryable after the mapping is fixed. This is automatic after a file appears in the watched folder; USB export/copy itself is not automated and this is not real-time network synchronization.

## Vendor material required to complete the real adapter

Request these items from Team Office for the exact terminal:

1. The full model suffix (for example, Z305W or Z305CB), serial number, and installed firmware version.
2. The **TeamOffice SDK** package from the official portal, including its release/version notes, supported-model matrix, documentation, samples, all required DLLs/native libraries, and runtime prerequisites/bitness.
3. A Z305-variant- and firmware-specific communication/API specification, including the supported transport, port, device authentication/communication-key behavior, framing, timeouts, and error responses.
4. The documented SDK/API operations and sample application for creating a user, initiating fingerprint enrollment/capture, checking enrollment, and deleting a user.
5. The documented attendance-log pull or event-subscription mechanism, including timestamp/timezone semantics, stable event IDs or cursors, acknowledgements, retry/reconnect behavior, and whether the device supports custom HTTP headers or request signing.
6. The matching Z305 user manual and network/push configuration guide.

The public download portal lists software and manuals but sends links only after a verified request. Exact Windows package names, database schema, and supported API are unknown from public material. The Z305 family catalog advertises USB and Data Push; USB exports can now be synchronized by the fallback above. The push transport/payload remains unverified, so the normalized webhook cannot yet be pointed at the terminal.

## Secure normalized webhook setup

1. Configure the terminal row's exact device ID and school in the authenticated AttendanceOS device page.
2. Generate a random secret of at least 32 characters and enter it as the webhook bearer token. AttendanceOS stores only its SHA-256 hash and does not return the token in configuration responses. Rotate it by entering a new value.
3. Send events over HTTPS with `Authorization: Bearer <configured-device-token>` and `Content-Type: application/json`.
4. The current normalized payload is:

   ```json
   {
     "device_id": "configured-device-id",
     "user_id": "0007",
     "timestamp": "2026-11-03T08:30:00+05:30",
     "verify_type": "FINGERPRINT",
     "event_id": "optional-device-event-id"
   }
   ```

   `device_id`, `user_id`, `timestamp`, and `verify_type` are required. IDs must be strings to preserve leading zeroes. Timestamps may be offset-aware ISO-8601 or naive ISO-8601 interpreted in `BIOMAX_DEVICE_TIMEZONE` (default `UTC`). Identical event IDs/payloads are rejected as duplicates.
5. If the device cannot set a bearer header, do not expose this unauthenticated from the public Internet. Put it behind a TLS reverse proxy/firewall that allowlists the terminal's fixed source IP, overwrites/injects the bearer header, blocks direct access to the Flask listener, and applies a shared rate limit. Configure `BIOMAX_DEVICE_TIMEZONE` for the terminal's local clock. The application limiter is per process; multi-worker deployments require proxy-level global rate limiting.
6. Authenticated administrators continue to use normal session authentication, RBAC, and CSRF protection. Device bearer authentication is only for the webhook path.

## Install and test on Windows

From the repository root in PowerShell:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_fingerprint_integration.py -v
```

These are software and mock-adapter tests. They do not prove communication, enrollment, or attendance synchronization with the physical Team Office terminal.

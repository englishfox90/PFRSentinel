# PFR Sentinel - N.I.N.A. plugin

N.I.N.A. plugin for PFR Sentinel (see `docs/NINA_INTEGRATION_PLAN.md` and the user
guide in `docs/wiki/NINA-Integration.md`):

- Imaging-tab panel: live pier-camera frame, health, imaging conditions, Start/Stop.
- Sequencer instructions: Start / Stop Sentinel Capture.
- Sequencer trigger **Sentinel: pause on bad conditions**
  (`PFRSentinel.NINA.SentinelSequenceItems.SentinelConditionsTrigger`) and loop
  condition **Sentinel: loop while conditions OK**
  (`PFRSentinel.NINA.SentinelSequenceItems.SentinelConditionsCondition`), both
  reading Sentinel's `GET /conditions`. These type names are saved in users'
  sequences and must never change.
- Target push: the running sequencer target to Sentinel's all-sky overlay.

Pure logic with no NINA dependency, written to be unit-testable from a console
harness: `Client/SentinelConditions.cs` (tolerant `/conditions` parser),
`SentinelSequenceItems/SentinelConditionsEvaluator.cs` (verdict + limits),
`SentinelConditionsText.cs` (reason codes in plain words), `SentinelConditionsWait.cs`
(settle / max-wait clock) and `SentinelConditionsSource.cs` (shared throttled cache).

Build: `dotnet build -c Release` (requires the .NET 8 SDK).
The PostBuild target copies the DLL to `%LOCALAPPDATA%\NINA\Plugins\3.0.0\PFRSentinel.NINA\`.
Close N.I.N.A. before rebuilding - it locks loaded plugin assemblies.

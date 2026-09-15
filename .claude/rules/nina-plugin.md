# NINA Plugin — C# / WPF Rules

`nina-plugin/PFRSentinel.NINA/` is the only non-Python code in this repo: a NINA
plugin (.NET 8 + WPF) that surfaces the pier camera inside NINA and drives
capture through Sentinel's HTTP control API.

**Most of this repo's conventions do not apply here, and most of its tooling
cannot see it.** Read this before editing anything under `nina-plugin/`.

## Build and deploy

```powershell
cd nina-plugin\PFRSentinel.NINA
dotnet build -c Release          # also deploys, see below
```

- Requires the **.NET 8 SDK**. .NET 8 reaches end-of-support **10 Nov 2026**, so
  a TFM migration is a scheduled item, not a surprise.
- `NINA.Plugin` is pinned to **3.2.0.9001**. Treat a NINA major/minor bump as
  planned maintenance — the plugin API moves between versions.
- The build's PostBuild target copies the DLL to
  `%LOCALAPPDATA%\NINA\Plugins\3.0.0\PFRSentinel.NINA\`.
- **NINA holds an open handle on a loaded plugin DLL.** Close NINA before
  building, or the deploy fails. It fails *loudly* by design — the stock
  template used `xcopy /c` ("continue on error"), which reported success while
  NINA kept running the previous build. Never reintroduce that.
- For a packaging build with NINA open, redirect the copy:
  `dotnet build -c Release -p:LOCALAPPDATA=<temp>`.

## The version folder is not NINA's version

Plugins load from `%LOCALAPPDATA%\NINA\Plugins\<PluginMinimumApplicationVersion>\`
— the plugin *compatibility baseline*, not NINA's own version. Verified: NINA
3.2.0.9001 loads from `Plugins\3.0.0\`. Reading `NINA.exe`'s FileVersion targets
a folder NINA never reads, and fails silently.

`services/nina_plugin_install.py` resolves it by enumerating the existing folders
and taking the highest. Keep both in step.

## Names that can never change

Saved sequences serialise fully-qualified type names, so renaming breaks users'
existing sequences:

- assembly / root namespace `PFRSentinel.NINA`
- `[Guid("e2e840b9-b5e2-4f67-8699-0173b5f9dc0a")]` on the plugin class
- `PFRSentinel.NINA.SentinelSequenceItems.{Start,Stop}SentinelCapture`

## Silent failure modes — the ones that cost the most time

- **MEF drops a broken export without any error.** If the plugin loads but a
  panel or instruction never appears, the constructor threw. Most often a
  mismatched `x:Key`, `x:Class`, or pack URI. `%LOCALAPPDATA%\NINA\Logs\` names
  the failing type.
- **DataTemplate keys are conventions**: `<FullyQualifiedTypeName>_Dockable`,
  `<AssemblyTitle>_Options`, `<TypeName>_Mini`. A typo is a silent no-show.
- **No `StaticResource` lookups inside a DataTemplate.** A missing key throws at
  load and manifests as the panel never appearing. Hand resolved values from the
  view model instead.
- **Resource dictionaries from every plugin are merged into one scope**, so keys
  must be plugin-prefixed or they collide across plugins.
- `Clone()` must copy every `[JsonProperty]`. The sequencer clones on drop, so a
  missed field works when built and loses its value on save/reload.
- `IValidatable` is `NINA.Sequencer.Validations`, `Issues` is get-only, and
  `SequenceItem` does **not** implement it — declare it explicitly. Implement
  `Issues` with a *notifying* setter; a plain auto-property never reaches the UI
  when NINA's background validation timer updates it.
- `Validate()` runs on a periodic timer for every item. It must not make
  requests, must not block, and must not throw.

## Talking to Sentinel

- The client reads `%LOCALAPPDATA%\PFRSentinel\config.json` directly for
  host/port/token — the same zero-config pairing the PowerShell helper uses. No
  sidecar file.
- Never use `EnsureSuccessStatusCode()`: 500/504 bodies carry the real cause.
- `503` has two meanings, separated only by the `code` field:
  `control_disabled` vs `control_unavailable`. They need opposite operator advice.
- Sentinel's ETag is an **unquoted MD5 hex digest** — not a valid entity tag.
  `EntityTagHeaderValue` rejects it and `Headers.ETag` returns null. Use
  `TryAddWithoutValidation` / `TryGetValues`. Getting this wrong fails silently:
  the 304 never fires and every poll re-downloads the frame.
- `HttpClient.Timeout` defaults to 100s but the server's `timeout` goes to 300.
- Cancelling a control request does **not** un-start capture — Sentinel has
  already executed the command by the time the socket can be aborted.
- Never log or display the token.

## Tooling coverage

The file-size hook covers `.cs`/`.xaml` (same 600 target / 750 hard cap). Nothing
else does: there is **no C# test suite, no linting, and pytest cannot see any of
this**. Verify changes by building and by exercising against a live Sentinel —
`scripts/nina/` and the plan's harness pattern show how to stand one up.

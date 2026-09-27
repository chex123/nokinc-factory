# Manufactured application testing — Expo and Maestro

**Decision:** include mobile testing as an optional, provider-neutral factory
capability. **Current status:** advisory Maestro adapter and CLI implemented;
native execution, authoritative CI ingestion, visual-provider integration and
app-specific flows remain unqualified until an actual mobile target is supplied.

## Where tests live, run and become mandatory

| Responsibility | Location / owner |
| --- | --- |
| Business examples and acceptance criteria | Work item plus approved QualityContract/design |
| Unit/component, API/contract, E2E flows, fixtures and visual baselines | Manufactured app repository; independent Test Author owns frozen acceptance artifacts |
| Fast developer feedback | Same tools from any IDE/terminal; local evidence is advisory |
| Authoritative application gates | Isolated target CI/factory workers, against exact candidate source and identified app build |
| Merge/release enforcement | Deterministic factory policy plus provider protection and required human gates |
| Factory's own tests | Factory repository; prove adapters/policies, not the behavior of manufactured apps |

The factory schedules and verifies tests; it need not host simulators in its API
process. Use an OS/capability-aware runner pool: macOS/Xcode for iOS, normally
Linux plus hardware-accelerated Android Emulator for Android. Expo EAS Workflows,
Maestro Cloud, GitHub Actions and customer-managed workers are alternative
adapters, not mandatory dependencies or an IDE restriction. No proprietary
editor integration or local AI model is needed just to run deterministic tests.

## Corrections to the supplied blueprint

1. **No “100% guarantee,” “zero flakiness” or infinite free retries.** Automation
   samples behavior; emulator/OS/network/graphics and test defects still exist.
   Local inference consumes hardware, electricity, latency and engineering time.
   Qualify models by measured outcomes, not a claimed 32B/70B or GB size.
2. **Test creation stays independent.** A flagship may design the architecture,
   but the Test Author creates/freezes acceptance flows without seeing the
   candidate implementation. Implementers do not fix failures by editing flows,
   fixtures, selectors' contracts or visual baselines to weaken assertions.
3. **No “retry until green,” auto-commit or ship-after-test shortcut.** Apply
   [ARP-1](AGENT-REVIEW-POLICY.md): original doer repairs, another model reviews,
   at least two completed reviews, bounded repairs/cost, same real failure twice
   stops. Independent review, exact-candidate gates and authority still apply.
4. **Maestro commands must match a pinned version.** Current documented permission
   controls use `launchApp.permissions` or `setPermissions`, not the supplied
   `allowPermission` command. Prefer explicit grant/deny/unset scenarios rather
   than automatically granting everything. `takeScreenshot.path` has no extension
   and resolves within the run's artifact tree, not necessarily the flow folder.
5. **A screenshot upload is not visual regression.** Current Maestro documents
   `assertScreenshot` comparison with a known baseline and threshold. This is a
   lean candidate default after version qualification. Percy/Applitools are
   optional supported-image comparison services requiring an actual adapter and
   approved credentials/data egress, not assumed drop-in screenshot uploads.
6. **MSW in React Native is not a browser service worker.** Current integration
   uses `setupServer` from `msw/native` and documented polyfills. Validate coverage
   for native networking libraries. WireMock is normally a separately running
   mock server, not automatically code inside the binary.
7. **Public Expo variables are compiled into client code.** A mock-mode flag is
   not an authorization/security boundary. Never embed secrets or allow a
   production app to enable bypass/mock behavior. Test-only builds must be
   isolated and distinguished from actual release candidates.
8. **The sample CI is incomplete.** It lacks dependency install, an Android build/
   emulator lane, verified build installation, device readiness, pinned tools,
   real visual comparison, and reliable artifact paths. Do not assume an iPhone
   model exists on a fixed runner image. Discover and select qualified runtime
   identifiers, check readiness and teardown only run-owned resources.
9. **Animations/permissions need coverage, not blanket disabling.** Deterministic
   visual fixtures may use reduced motion, but include realistic animation,
   keyboard, accessibility text size, localization, theme and denied-permission
   cases. Importing `AccessibilityInfo` alone disables nothing.

## Three complementary mobile lanes

| Lane | What it proves | What it cannot replace |
| --- | --- | --- |
| Deterministic UI/visual | Seeded data/clock/IDs and approved mock responses; success/error/offline/boundary flows, baseline comparison | Real backend/auth/network contracts, production package behavior |
| Real integration | Built app with mocks OFF talks to disposable/sandbox API/database/auth; verify server-side side effects, idempotency and persistence | Device/OS coverage and platform-specific release qualification |
| Release qualification | Production-configured platform packages, signing/permissions/deep links/push/native-module/device checks as applicable | Authorization, store review/policies, backend recovery and human approvals |

An Android APK and iOS simulator `.app` are not the exact store AAB/IPA package.
Bind source/build configuration/toolchain identities across variants and test
release packages appropriately. Likewise, mocked UI and real-integration binaries
are distinct artifacts. Never label simulator/mock evidence as proof the exact
shipping package passed. Include Expo OTA update/runtime-version/channel identity
and compatibility if updates are part of the product's delivery surface.

Use stable `testID`/accessibility identifiers rather than coordinate taps; verify
accessibility semantics separately. Reset app/keychain/backend fixtures as needed;
`clearState` does not promise to erase external accounts, keychains or a database.
Use bounded readiness assertions, not arbitrary sleeps. Record every attempt;
an infrastructure rerun must not erase prior test failures or consume a new budget.

## Factory integration implemented now

- [Mobile config/result](../src/nokinc_factory/domain/mobile.py): platform, explicit
  device, expected test-name inventory, bounded timeout and normalized flow path.
- [Maestro adapter](../src/nokinc_factory/adapters/maestro.py): universal
  `mobile_e2e` gate, exact argv/no shell interpolation, explicit output directories,
   limited default environment, one attempt and safe unavailable/failure outcomes.
   The timeout bounds the subprocess; input/report ingestion has separate byte,
   entry, depth and element caps, not a wall-clock SLA. The worker sets the overall
   job deadline and CPU/disk/process-tree limits.
- [Input hashing](../src/nokinc_factory/adapters/maestro_inputs.py): bounded complete
  declared flow tree, including nested scripts/fixtures/baselines; refuse
  symlinks/junctions and detect ordinary changes across the run.
- [Report parsing](../src/nokinc_factory/adapters/maestro_reports.py): new UTF-8
  JUnit report only; expected test inventory exactly matches; reject empty,
  duplicate, unknown, malformed/DTD, inconsistent counts and missing tests.
   Unknown element structure and substituted artifact directories also fail.
   Nonzero exit or failed/skipped tests cannot pass.
- [CLI](../src/nokinc_factory/cli.py): `factory mobile-test`, with explicit
  `--platform`, `--device`, repeated `--expected-test`, `--repo`, optional flow/
  artifact paths and bounded timeout. Exit 0=local pass, 1=failure, 2=unavailable/
  bad configuration. `factory mobile-test --help` describes the arguments.

All returned mobile results have `evidence_scope=local_advisory`. Neither a CLI
flag nor a caller-supplied “CI=true” promotes them to release authority. The
adapter does not install/attest the binary, start a device, run a provider build,
compare screenshots as a separate gate, create baselines, upload evidence, or
retry. Requesting the `visual` gate returns `NOT_AVAILABLE`, not screenshot PASS.

This first adapter profile supports provisioned Linux/Android and macOS workers.
Maestro itself now supports native Windows, but Windows launcher/device behavior
has not been qualified by this adapter and returns `NOT_AVAILABLE`. A missing
tool, device, build or runtime cannot be converted into a successful test result.

Flow YAML may execute scripts or network requests. `shell=False`, path checks and
input hashing are **not a sandbox or an atomic read-only snapshot**. Run only in
an already isolated worker without production credentials; mount approved inputs
read-only and keep logs/screenshots in restricted tenant-scoped storage. The
runner enforces process-tree/resource/egress cleanup and storage quotas. Raw
reports may contain sensitive UI data; do not forward them to models/providers
without tenant policy and redaction. Local results never prove true app identity.

## Remaining authoritative implementation and exit criteria

1. A05/A07: mobile capability manifest/schema, immutable test-contract inventory
   and baseline authority; target CI runs required `mobile_e2e`, real integration,
   visual and other gates according to applicability. Freeze `.maestro` flows,
   helper scripts, fixtures and baselines under independent ownership.
2. A07: build/install adapters and OS worker leases, pinned Maestro/Java/Expo/
   Xcode/Android system images; verify build hashes, app ID, install receipt,
   device/runtime, flow/fixture/config/baseline digests, source SHA and run ID.
   Bind and authenticate reports at the trusted pipeline boundary. Preserve all
   attempt outcomes and deny stale/forged or incomplete platform matrix evidence.
3. B04: visual provider or version-qualified native `assertScreenshot`, approved
   baseline/threshold/masks and theme/locale/device matrix; missing/new baseline
   blocks pending design-owner approval. The implementer cannot accept its own
   screenshot changes. Do not buy both commercial visual products by default.
4. B07: demonstrate actual Android **and** iOS runs for an approved Expo app:
   at least one happy path and negative/backend-error/permission/relaunch case;
   inject a real UI/layout defect and observe failure, repair without changing
   frozen tests and observe pass. Run the real-backend and release-package lanes.
5. Simulate missing emulator, changed binary/source, hidden/skipped flow, stale
   report/baseline, platform matrix gap, test-only mock code in a release,
   timeout/worker crash and cross-tenant artifact access. All fail closed.

**Current environment:** Windows, Java and ADB available; Maestro and a configured
Android emulator not found; no Expo target in this workspace. iOS needs an
authorized macOS runner. No mobile emulator or paid EAS/Maestro/visual provider
run has occurred. Factory adapter/unit/subprocess tests are not mobile E2E proof.

## Verified documentation (12 September 2026)

- [Expo: EAS Workflows with Maestro](https://docs.expo.dev/eas/workflows/examples/e2e-tests/)
- [Expo public environment variables](https://docs.expo.dev/guides/environment-variables/)
- [Maestro CLI options](https://docs.maestro.dev/maestro-cli/maestro-cli-commands-and-options.md)
- [Maestro permissions](https://docs.maestro.dev/maestro-flows/flow-control-and-logic/permissions.md)
- [Maestro report/artifact locations](https://docs.maestro.dev/maestro-flows/workspace-management/test-reports-and-artifacts.md)
- [Maestro native visual comparison](https://docs.maestro.dev/reference/commands-available/assertscreenshot.md)
- [MSW React Native integration](https://mswjs.io/docs/integrations/react-native/)

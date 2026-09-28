/**
 * Opt-in offline regressions against the actual privileged review JavaScript.
 * Every provider/model operation is replaced with an in-memory double. No real
 * credential, network, GitHub write, workflow dispatch, or model call is used.
 * This is not proof of GitHub-hosted protection or a security sandbox.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const localRequire = createRequire(import.meta.url);
const workflowUrl = new URL('../../../workflows/cross-model-review.yml', import.meta.url);
const workflow = await readFile(workflowUrl, 'utf8');
const marker = '          script: |';
const block = workflow.slice(workflow.indexOf(marker) + marker.length).trimStart();
assert.ok(workflow.includes(marker), 'Expected the existing github-script block');
const script = block.split('\n').map(line => line.replace(/^ {12}/, '')).join('\n');
const taskField = 'Factory-Task: https://github.com/audit/synthetic/issues/2';
const requiredDeterministicSteps = [
  'build', 'types', 'lint', 'unit', 'acceptance', 'diff_coverage', 'secret_scan', 'dependency_scan',
];
const successfulGateJobs = [
  {
    name: 'deterministic-gates',
    status: 'completed',
    conclusion: 'success',
    steps: requiredDeterministicSteps.map(name => ({
      name,
      status: 'completed',
      conclusion: 'success',
    })),
  },
  { name: 'baseline-assertion', status: 'completed', conclusion: 'success', steps: [] },
  { name: 'frozen-contract', status: 'completed', conclusion: 'success', steps: [] },
];

async function simulateReview({ staleHead = false, missingTask = false, gaps = [],
    resolvedModel = 'configured-reviewer', mutateHeadDuringReview = false,
    unavailableRules = false, unavailableIssue = false, diffData = null,
    gateJobs = successfulGateJobs, unavailableGateJobs = false,
    missingPRLinks = false, associatedPRs = null, mergedPR = false,
    mergedFiles = [{ filename: 'a.txt', status: 'modified' }],
    currentTreeFiles = { 'a.txt': 'Synthetic current main source\n' },
    unavailableCurrentTree = false, mutateCurrentBranchDuringReview = false,
    mismatchedCurrentTreePath = false, mismatchedCurrentTreeBlob = false,
    linkedPullRequest = false, changedTaskField = null, changedSourceField = null,
    payloadOverride = undefined, issueBody = 'Synthetic data', prBody = null } = {}) {
  const statuses = [];
  const failures = [];
  const comments = [];
  const requests = [];
  const issueRequests = [];
  const gateRunRequests = [];
  const pullFileRequests = [];
  const branchRequests = [];
  const currentSourceRequests = [];
  const modelInputs = [];
  let modelCalls = 0;
  const testedSha = 'a'.repeat(40);
  let currentSha = staleHead ? 'b'.repeat(40) : testedSha;
  let currentBase = 'c'.repeat(40);
  let currentBaseBranchSha = 'f'.repeat(40);
  let currentBody = prBody ?? (missingTask ? '' : `${taskField}\n\nSynthetic change`);
  const context = {
    repo: { owner: 'audit', repo: 'synthetic' },
    runId: 1,
    payload: { workflow_run: {
      id: 3,
      run_attempt: 2,
      name: 'gates',
      head_sha: testedSha,
      conclusion: 'success',
      event: 'pull_request',
      pull_requests: missingPRLinks ? [] : [{ number: 1, head: { sha: testedSha } }],
    } },
  };
  const github = {
    rest: {
      actions: {
        listJobsForWorkflowRunAttempt: async args => {
          gateRunRequests.push(args);
          if (unavailableGateJobs) throw new Error('synthetic unavailable gate results');
          return { data: { jobs: gateJobs } };
        },
      },
      pulls: {
        get: async () => ({ data: {
          head: { sha: currentSha },
          base: { sha: currentBase, ref: 'main' },
          body: currentBody,
          merged: mergedPR,
        } }),
        listFiles: async args => {
          pullFileRequests.push(args);
          return { data: mergedFiles };
        },
      },
      repos: {
        createCommitStatus: async value => { statuses.push(value); },
        getBranch: async args => {
          branchRequests.push(args);
          return { data: { name: args.branch, commit: { sha: currentBaseBranchSha } } };
        },
        getContent: async args => {
          if (args.path === '.github/copilot-instructions.md') {
            if (unavailableRules) throw new Error('synthetic unavailable rules');
            return { data: {
              content: Buffer.from('Synthetic invariant: no external actions').toString('base64'),
              encoding: 'base64',
            } };
          }
          currentSourceRequests.push(args);
          if (unavailableCurrentTree) {
            throw Object.assign(new Error('synthetic current source unavailable'), { status: 503 });
          }
          const source = currentTreeFiles[args.path];
          if (typeof source !== 'string') {
            throw Object.assign(new Error('synthetic current path absent'), { status: 404 });
          }
          return { data: {
            type: 'file',
            path: mismatchedCurrentTreePath ? 'different.txt' : args.path,
            sha: mismatchedCurrentTreeBlob ? '1'.repeat(40) : createHash('sha1')
              .update(`blob ${Buffer.byteLength(source, 'utf8')}\0`, 'utf8')
              .update(Buffer.from(source, 'utf8'))
              .digest('hex'),
            size: Buffer.byteLength(source, 'utf8'),
            content: Buffer.from(source, 'utf8').toString('base64'),
            encoding: 'base64',
          } };
        },
      },
      issues: {
        get: async args => {
          issueRequests.push(args);
          if (unavailableIssue) throw new Error('synthetic unavailable issue');
          const data = {
            number: 2, html_url: 'https://github.com/audit/synthetic/issues/2',
            title: 'Synthetic task', body: issueBody, labels: [],
          };
          if (linkedPullRequest) data.pull_request = { url: 'https://api.github.com/pulls/2' };
          if (modelCalls && changedTaskField) {
            data[changedTaskField] = changedTaskField === 'labels' ? ['changed'] : 'changed';
          }
          return { data };
        },
        createComment: async value => { comments.push(value); },
      },
    },
    request: async (route, options) => {
      requests.push({ route, options });
      if (route === 'GET /repos/{owner}/{repo}/commits/{commit_sha}/pulls') {
        return { data: associatedPRs ?? [{ number: 1, head: { sha: testedSha } }] };
      }
      return { data: diffData ?? 'diff --git a/a.txt b/a.txt\n+synthetic change\n' };
    },
  };
  const sandbox = {
    github, context, Buffer, AbortSignal, URL, require: localRequire,
    core: { setFailed: message => { failures.push(message); } },
    process: { env: {
      OPENAI_API_KEY: 'synthetic-not-a-credential',
      REVIEW_MODEL: 'configured-reviewer',
      MAX_DIFF_BYTES: '120000',
      MAX_CURRENT_SOURCE_BYTES: '80000',
      GITHUB_SERVER_URL: 'https://github.com',
    } },
    fetch: async (_url, request) => {
      modelCalls++;
      modelInputs.push(JSON.parse(request.body).input);
      if (mutateHeadDuringReview) currentSha = 'd'.repeat(40);
      if (changedSourceField === 'base') currentBase = 'e'.repeat(40);
      if (changedSourceField === 'body') currentBody = 'Other task #3';
      if (mutateCurrentBranchDuringReview) currentBaseBranchSha = 'e'.repeat(40);
      return {
        ok: true,
        json: async () => payloadOverride !== undefined ? payloadOverride : ({
          status: 'completed', model: resolvedModel,
          output: [{ type: 'message', content: [{ type: 'output_text',
            text: JSON.stringify({ verdict: 'ACCEPT', findings: [], context_gaps: gaps }),
          }] }],
        }),
      };
    },
  };
  await vm.runInNewContext(`(async () => {\n${script}\n})()`, sandbox, { timeout: 1000 });
  return { statuses, failures, comments, modelCalls, testedSha, currentSha,
    requests, issueRequests, gateRunRequests, pullFileRequests, branchRequests,
    currentSourceRequests, modelInputs };
}

function assertBlocked(result, explanation) {
  assert.equal(result.statuses.some(status => status.state === 'success'), false, explanation);
  assert.equal(result.statuses.at(-1)?.state, 'failure', 'Must report a terminal blocking outcome');
  assert.ok(result.failures.length > 0, 'Must explain the blocking outcome');
}

function readDiffProvenance(input) {
  const heading = '## VERIFIED DIFF PROVENANCE (GitHub API source and exact diff bytes)';
  const start = input.indexOf(heading);
  const end = input.indexOf('\n## PR DIFF', start);
  assert.ok(start >= 0, 'Expected verified diff provenance in reviewer input');
  assert.ok(end > start, 'Expected provenance to precede the supplied PR diff');
  return JSON.parse(input.slice(start + heading.length, end).trim());
}

function readCurrentTreeEvidence(input) {
  const heading = '## VERIFIED CURRENT TREE EVIDENCE (read-only GitHub API at pinned branch commit)';
  const start = input.indexOf(heading);
  const end = input.indexOf('\n## VERIFIED DIFF PROVENANCE', start);
  assert.ok(start >= 0, 'Expected verified current-tree evidence in reviewer input');
  assert.ok(end > start, 'Expected current-tree evidence to precede the supplied PR diff');
  return JSON.parse(input.slice(start + heading.length, end).trim());
}

test('valid complete evidence still receives success on the tested head', async () => {
  const result = await simulateReview();
  assert.equal(result.statuses.at(-1)?.state, 'success');
  assert.equal(result.statuses.at(-1)?.sha, result.testedSha);
  assert.deepEqual(result.failures, []);
  assert.equal(result.modelCalls, 1);
  assert.equal(result.gateRunRequests.length, 1);
  assert.equal(result.gateRunRequests[0].run_id, 3);
  assert.equal(result.gateRunRequests[0].attempt_number, 2);
  assert.match(result.modelInputs[0], /VERIFIED CI EVIDENCE/);
  assert.match(result.modelInputs[0], /"tested_sha": "a{40}"/);
  assert.match(result.modelInputs[0], /TASK CONTEXT/);
  assert.match(result.modelInputs[0], /Synthetic data/);
  assert.match(result.modelInputs[0], /PR DESCRIPTION/);
  assert.match(result.modelInputs[0], /Synthetic change/);
  for (const stepName of requiredDeterministicSteps) {
    assert.ok(result.modelInputs[0].includes(`"${stepName}": "success"`),
      `Expected verified success for ${stepName} in the model input`);
  }
});

test('failed required CI job or step blocks before a model call', async () => {
  const gateJobs = structuredClone(successfulGateJobs);
  gateJobs[0].steps.find(step => step.name === 'unit').conclusion = 'failure';

  const result = await simulateReview({ gateJobs });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'A failed required CI step must not reach the reviewer model');
});

test('missing required CI job or step blocks before a model call', async () => {
  const gateJobs = structuredClone(successfulGateJobs);
  gateJobs[0].steps = gateJobs[0].steps.filter(step => step.name !== 'secret_scan');

  const result = await simulateReview({ gateJobs });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'An incomplete CI evidence set must not reach the reviewer model');
});

test('missing required CI job blocks before a model call', async () => {
  const gateJobs = successfulGateJobs.filter(job => job.name !== 'baseline-assertion');

  const result = await simulateReview({ gateJobs });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'A missing required CI job must not reach the reviewer model');
});

test('unavailable CI run evidence blocks before a model call', async () => {
  const result = await simulateReview({ unavailableGateJobs: true });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'Unavailable gate evidence must not be assumed successful');
});

test('merged gate rerun resolves its PR from the tested commit when links are absent', async () => {
  const result = await simulateReview({ missingPRLinks: true, mergedPR: true });

  assert.equal(result.statuses.at(-1)?.state, 'success');
  assert.deepEqual(result.failures, []);
  assert.equal(result.modelCalls, 1);
  assert.equal(
    result.requests[0].route,
    'GET /repos/{owner}/{repo}/commits/{commit_sha}/pulls',
  );
  assert.equal(result.requests[0].options.commit_sha, result.testedSha);
  assert.equal(
    result.requests[1].route,
    'GET /repos/{owner}/{repo}/pulls/{pull_number}',
  );
  assert.equal(result.requests[1].options.pull_number, 1);
});

test('commit association fallback ignores PRs with a different tested head', async () => {
  const result = await simulateReview({
    missingPRLinks: true,
    associatedPRs: [{ number: 2, head: { sha: 'b'.repeat(40) } }],
  });

  assert.equal(result.modelCalls, 0);
  assert.ok(result.failures.includes('Exactly one valid triggering PR is required'));
});

test('ambiguous commit-to-PR fallback fails closed before a model call', async () => {
  const result = await simulateReview({
    missingPRLinks: true,
    associatedPRs: [
      { number: 1, head: { sha: 'a'.repeat(40) } },
      { number: 2, head: { sha: 'a'.repeat(40) } },
    ],
  });

  assert.equal(result.modelCalls, 0);
  assert.ok(result.failures.includes('Exactly one valid triggering PR is required'));
});

test('review must not approve a head different from the successful gate run', async () => {
  const result = await simulateReview({ staleHead: true });
  assertBlocked(result,
    `Untested head ${result.currentSha} received success from run for ${result.testedSha}`);
});

test('missing authoritative task context must block review acceptance', async () => {
  const result = await simulateReview({ missingTask: true });
  assertBlocked(result,
    'Review succeeded with no linked story/design');
});

test('unresolved context gaps must not be accepted as complete evidence', async () => {
  const result = await simulateReview({ gaps: ['Required acceptance contract is missing'] });
  assertBlocked(result,
    'ACCEPT plus an unresolved required-context gap received success');
});

test('context-gap failure reports bounded text with sensitive content redacted', async () => {
  const fakeGitHubToken = ['ghp', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'].join('_');
  const gap = 'Missing contract for reviewer@example.com +15551234567 ' +
    `${fakeGitHubToken} api_key=local-super-secret-value ` +
    'reviewer Mary Jones @chex123 **notify**';
  const result = await simulateReview({ gaps: [gap] });

  assertBlocked(result, 'A context gap must remain a blocking outcome');
  assert.equal(result.comments.length, 1);
  const body = result.comments[0].body;
  assert.match(body, /Missing contract/);
  assert.match(body, /REDACTED EMAIL/);
  assert.match(body, /REDACTED PHONE/);
  assert.match(body, /REDACTED SECRET/);
  assert.match(body, /REDACTED NAME/);
  assert.doesNotMatch(body,
    /reviewer@example\.com|\+15551234567|ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ|local-super-secret-value|Mary Jones/);
  assert.doesNotMatch(body, /@chex123|\*\*notify\*\*/);
});

test('context-gap report caps the number and length of details', async () => {
  const gaps = Array.from({ length: 10 }, (_, index) =>
    `gap-${index}: ${'x'.repeat(800)}`);
  const result = await simulateReview({ gaps });

  assertBlocked(result, 'Excessive context gaps must remain blocking');
  const body = result.comments[0].body;
  assert.match(body, /Showing first 5 of 10/);
  assert.match(body, /gap-0/);
  assert.doesNotMatch(body, /gap-5:/);
  assert.ok(body.length <= 4_000, 'Diagnostic comment must be bounded');
});

test('unqualified resolved model identity must block acceptance', async () => {
  const result = await simulateReview({ resolvedModel: 'unexpected-model-family' });
  assertBlocked(result,
    'Unexpected resolved model was displayed but not rejected');
});

test('review fetches immutable base/head diff instead of floating PR content', async () => {
  const result = await simulateReview();
  assert.equal(result.requests[0].route, 'GET /repos/{owner}/{repo}/compare/{base}...{head}');
  assert.equal(result.requests[0].options.base, 'c'.repeat(40));
  assert.equal(result.requests[0].options.head, result.testedSha);
  assert.equal(result.branchRequests.length, 0);
  assert.equal(result.pullFileRequests.length, 0);
  assert.doesNotMatch(result.modelInputs[0], /VERIFIED CURRENT TREE EVIDENCE/);
});

test('reviewer input identifies the exact diff body and immutable source commits', async () => {
  const diffData = 'diff --git a/model_pricing.py b/model_pricing.py\n' +
    '--- a/model_pricing.py\n+++ b/model_pricing.py\n@@\n-rate\n+known date\n';
  const result = await simulateReview({ diffData });
  const expectedDigest = createHash('sha256').update(diffData, 'utf8').digest('hex');

  assert.deepEqual(readDiffProvenance(result.modelInputs[0]), {
    route: 'GET /repos/{owner}/{repo}/compare/{base}...{head}',
    pull_number: 1,
    media_type: 'application/vnd.github.v3.diff',
    base_sha: 'c'.repeat(40),
    head_sha: result.testedSha,
    tested_sha: result.testedSha,
    diff_bytes: Buffer.byteLength(diffData, 'utf8'),
    diff_sha256: expectedDigest,
  });
});

test('merged review provenance identifies the pinned pull diff and tested head', async () => {
  const diffData = 'diff --git a/model_pricing.py b/model_pricing.py\n+known date\n';
  const result = await simulateReview({ diffData, mergedPR: true, missingPRLinks: true });
  const expectedDigest = createHash('sha256').update(diffData, 'utf8').digest('hex');

  assert.deepEqual(readDiffProvenance(result.modelInputs[0]), {
    route: 'GET /repos/{owner}/{repo}/pulls/{pull_number}',
    pull_number: 1,
    media_type: 'application/vnd.github.v3.diff',
    head_sha: result.testedSha,
    tested_sha: result.testedSha,
    diff_bytes: Buffer.byteLength(diffData, 'utf8'),
    diff_sha256: expectedDigest,
  });
  const diffRequest = result.requests.find(request =>
    request.route === 'GET /repos/{owner}/{repo}/pulls/{pull_number}');
  assert.equal(diffRequest?.options.pull_number, 1);
});

test('merged review includes commit-pinned current source snapshots with verified digests', async () => {
  const sourcePath = 'src/nokinc_factory/application/model_pricing.py';
  const source = [
    'known_from=date(2026, 9, 28),',
    'provider_effective_from=None,',
    'provider_effective_until=None,',
  ].join('\n');
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    mergedFiles: [{ filename: sourcePath, status: 'modified' }],
    currentTreeFiles: { [sourcePath]: source },
  });
  const currentTree = readCurrentTreeEvidence(result.modelInputs[0]);

  assert.deepEqual(currentTree, {
    branch: 'main',
    commit_sha: 'f'.repeat(40),
    files: [{
      path: sourcePath,
      state: 'present',
      blob_sha: createHash('sha1')
        .update(`blob ${Buffer.byteLength(source, 'utf8')}\0`, 'utf8')
        .update(Buffer.from(source, 'utf8'))
        .digest('hex'),
      byte_count: Buffer.byteLength(source, 'utf8'),
      sha256: createHash('sha256').update(source, 'utf8').digest('hex'),
      content: source,
    }],
  });
  assert.equal(result.pullFileRequests[0].pull_number, 1);
  assert.deepEqual(result.branchRequests.map(request => request.branch), ['main', 'main']);
  assert.deepEqual(result.currentSourceRequests.map(request => ({ path: request.path, ref: request.ref })), [
    { path: sourcePath, ref: 'f'.repeat(40) },
  ]);
});

test('unavailable merged current-tree evidence blocks before model invocation', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    unavailableCurrentTree: true,
  });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'Unavailable current-tree evidence must fail closed');
});

test('mismatched merged current-tree path blocks before model invocation', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    mismatchedCurrentTreePath: true,
  });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'A different current-tree file must not satisfy the requested path');
});

test('mismatched merged current-tree blob SHA blocks before model invocation', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    mismatchedCurrentTreeBlob: true,
  });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'Current-tree blob identity must match the returned bytes');
});

test('merged current-tree evidence records paths absent at the pinned branch commit', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    mergedFiles: [{ filename: 'removed.txt', status: 'removed' }],
    currentTreeFiles: {},
  });

  assert.deepEqual(readCurrentTreeEvidence(result.modelInputs[0]), {
    branch: 'main',
    commit_sha: 'f'.repeat(40),
    files: [{ path: 'removed.txt', state: 'absent' }],
  });
});

test('merged current-tree file-count limit fails closed before model invocation', async () => {
  const mergedFiles = Array.from({ length: 100 }, (_, index) => ({
    filename: `file-${index}.txt`,
    status: 'modified',
  }));
  const result = await simulateReview({ mergedPR: true, missingPRLinks: true, mergedFiles });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'Incomplete current-tree file list must fail closed');
});

test('merged current-tree byte limit fails closed without truncating source', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    currentTreeFiles: { 'a.txt': 'x'.repeat(80_001) },
  });

  assert.equal(result.modelCalls, 0);
  assertBlocked(result, 'Oversized current-tree evidence must fail closed');
});

test('merged review blocks if the target branch moves during model review', async () => {
  const result = await simulateReview({
    mergedPR: true,
    missingPRLinks: true,
    mutateCurrentBranchDuringReview: true,
  });

  assert.equal(result.modelCalls, 1);
  assertBlocked(result, 'A target branch change during current-tree review must block success');
});

test('candidate cannot rewrite privileged gate or repository control files', async () => {
  const result = await simulateReview({
    diffData: 'diff --git a/.github/workflows/gates.yml b/.github/workflows/gates.yml\n' +
      '--- a/.github/workflows/gates.yml\n+++ b/.github/workflows/gates.yml\n' +
      '@@\n+changed control plane\n',
  });
  assertBlocked(result, 'PR-controlled gate workflow was allowed into privileged review');
  assert.equal(result.modelCalls, 0);
});

test('a head change during model review blocks success', async () => {
  assertBlocked(await simulateReview({ mutateHeadDuringReview: true }), 'Review head changed');
});

test('unavailable pinned repository rules cannot silently use generic instructions', async () => {
  const result = await simulateReview({ unavailableRules: true });
  assertBlocked(result, 'Required base rules were unavailable');
  assert.equal(result.modelCalls, 0);
});

test('unavailable linked issue cannot silently be omitted', async () => {
  const result = await simulateReview({ unavailableIssue: true });
  assertBlocked(result, 'Required linked issue was unavailable');
  assert.equal(result.modelCalls, 0);
});

test('nontext diff data cannot be reviewed as an object string', async () => {
  const result = await simulateReview({ diffData: { error: 'unexpected JSON' } });
  assertBlocked(result, 'Malformed diff was converted to generic string');
  assert.equal(result.modelCalls, 0);
});

test('linked pull request cannot substitute for a work item', async () => {
  const result = await simulateReview({ linkedPullRequest: true });
  assertBlocked(result, 'PR response satisfied required issue');
  assert.equal(result.modelCalls, 0);
});

for (const diffData of ['not a diff',
  'diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n']) {
  test(`unsupported textual diff is blocked: ${diffData.slice(0, 25)}`, async () => {
    const result = await simulateReview({ diffData });
    assertBlocked(result, 'Opaque content lacks reviewable evidence');
    assert.equal(result.modelCalls, 0);
  });
}

test('HTTP-success provider error diagnostics are not published', async () => {
  const result = await simulateReview({ payloadOverride: {
    model: 'configured-reviewer', status: 'failed', error: { message: 'private-provider-sentinel' },
  } });
  assertBlocked(result, 'Failed provider response accepted');
  assert.equal(JSON.stringify(result).includes('private-provider-sentinel'), false);
});

for (const payloadOverride of [null, { model: 'configured-reviewer', status: 'completed', output: {} }]) {
  test('malformed provider envelope reports terminal failure', async () => {
    assertBlocked(await simulateReview({ payloadOverride }), 'Malformed envelope lacks failure');
  });
}

for (const changedTaskField of ['title', 'labels', 'body']) {
  test(`task ${changedTaskField} changing during review invalidates success`, async () => {
    assertBlocked(await simulateReview({ changedTaskField }), 'Task changed');
  });
}

for (const changedSourceField of ['base', 'body']) {
  test(`PR ${changedSourceField} changing during review invalidates success`, async () => {
    assertBlocked(await simulateReview({ changedSourceField }), 'Source changed');
  });
}

test('task context cannot exceed the input byte budget', async () => {
  const result = await simulateReview({ issueBody: 'x'.repeat(200_001) });
  assertBlocked(result, 'Unbounded task context was sent to model');
  assert.equal(result.modelCalls, 0);
});

for (const prBody of ['Closes other/project#2', 'Refs local #2 and other/project#3',
  'Closes https://github.com/other/project/issues/2#2',
  'Closes https://github.com/other/project/issues/2?x=:#2']) {
  test(`nonlocal task reference cannot be rebound locally: ${prBody}`, async () => {
    const result = await simulateReview({ prBody });
    assertBlocked(result, 'Nonlocal task was rebound to the current repository');
    assert.equal(result.modelCalls, 0);
  });
}

for (const suffix of ['', '\n\nCloses unrelated/project#39',
  '\r\n\r\nReference only: https://github.com/other/project/issues/2?x=:#2',
  '\rNotes using a standalone carriage return']) {
  test(`explicit task identity is the only source of issue lookup: ${JSON.stringify(suffix)}`, async () => {
    const result = await simulateReview({ prBody: taskField + suffix });
    assert.equal(result.statuses.at(-1)?.state, 'success');
    assert.equal(result.issueRequests.length, 2, 'read then revalidate exactly the named task');
    assert.ok(result.issueRequests.every(args =>
      args.owner === 'audit' && args.repo === 'synthetic' && args.issue_number === 2));
  });
}

for (const prBody of [
  'Closes #2',
  `Some prose\n${taskField}`,
  `\`\`\`text\n${taskField}\n\`\`\``,
  `<!--\n${taskField}\n-->`,
  ` ${taskField}`,
  `${taskField}\n${taskField}`,
  `${taskField}\n factory-task: https://github.com/audit/synthetic/issues/3`,
  `${taskField}\nNotes\rfactory-task: https://github.com/audit/synthetic/issues/3`,
  `${taskField} trailing text`,
  'Factory-Task: https://github.com/other/project/issues/2',
  'Factory-Task: https://github.com/audit/synthetic/pull/2',
  'Factory-Task: https://github.com/audit/synthetic/issues/02',
  'Factory-Task: https://github.com/audit/synthetic/issues/0',
  'Factory-Task: https://github.com/audit/synthetic/issues/9007199254740992',
  'Factory-Task: https://github.com/audit/synthetic/issues/2?x=:#2',
  'Factory-Task: https://github.com/audit/synthetic/issues/2#2',
  'Factory-Task: https://github.com/audit/synthetic/issues/%32',
  'Factory-Task: https://github.com/audit/synthetic/x/../issues/2',
  'Factory-Task: https://github.com:443/audit/synthetic/issues/2',
  'Factory-Task: https://user@github.com/audit/synthetic/issues/2',
  'Factory-Task: http://github.com/audit/synthetic/issues/2',
  'Factory-Task: https://github.com.invalid/audit/synthetic/issues/2',
  'Factory-Task: https://github.com/audit/synthetic/issues/2/',
]) {
  test(`malformed or unauthorized explicit task is rejected: ${JSON.stringify(prBody)}`, async () => {
    const result = await simulateReview({ prBody });
    assertBlocked(result, 'Invalid explicit task selected authority-bearing context');
    assert.equal(result.modelCalls, 0);
    assert.equal(result.issueRequests.length, 0, 'invalid identity must not fetch any issue');
  });
}
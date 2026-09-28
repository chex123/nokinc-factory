/**
 * Opt-in offline regressions against the actual privileged review JavaScript.
 * Every provider/model operation is replaced with an in-memory double. No real
 * credential, network, GitHub write, workflow dispatch, or model call is used.
 * This is not proof of GitHub-hosted protection or a security sandbox.
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const workflowUrl = new URL('../../../workflows/cross-model-review.yml', import.meta.url);
const workflow = await readFile(workflowUrl, 'utf8');
const marker = '          script: |';
const block = workflow.slice(workflow.indexOf(marker) + marker.length).trimStart();
assert.ok(workflow.includes(marker), 'Expected the existing github-script block');
const script = block.split('\n').map(line => line.replace(/^ {12}/, '')).join('\n');
const taskField = 'Factory-Task: https://github.com/audit/synthetic/issues/2';

async function simulateReview({ staleHead = false, missingTask = false, gaps = [],
    resolvedModel = 'configured-reviewer', mutateHeadDuringReview = false,
    unavailableRules = false, unavailableIssue = false, diffData = null,
  missingPRLinks = false, associatedPRs = null, mergedPR = false,
    linkedPullRequest = false, changedTaskField = null, changedSourceField = null,
    payloadOverride = undefined, issueBody = 'Synthetic data', prBody = null } = {}) {
  const statuses = [];
  const failures = [];
  const comments = [];
  const requests = [];
  const issueRequests = [];
  let modelCalls = 0;
  const testedSha = 'a'.repeat(40);
  let currentSha = staleHead ? 'b'.repeat(40) : testedSha;
  let currentBase = 'c'.repeat(40);
  let currentBody = prBody ?? (missingTask ? '' : `${taskField}\n\nSynthetic change`);
  const context = {
    repo: { owner: 'audit', repo: 'synthetic' },
    runId: 1,
    payload: { workflow_run: {
      head_sha: testedSha,
      conclusion: 'success',
      event: 'pull_request',
      pull_requests: missingPRLinks ? [] : [{ number: 1, head: { sha: testedSha } }],
    } },
  };
  const github = {
    rest: {
      pulls: { get: async () => ({ data: {
        head: { sha: currentSha },
        base: { sha: currentBase },
        body: currentBody,
        merged: mergedPR,
      } }) },
      repos: {
        createCommitStatus: async value => { statuses.push(value); },
        getContent: async () => {
          if (unavailableRules) throw new Error('synthetic unavailable rules');
          return { data: {
            content: Buffer.from('Synthetic invariant: no external actions').toString('base64'),
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
    github, context, Buffer, AbortSignal, URL,
    core: { setFailed: message => { failures.push(message); } },
    process: { env: {
      OPENAI_API_KEY: 'synthetic-not-a-credential',
      REVIEW_MODEL: 'configured-reviewer',
      MAX_DIFF_BYTES: '120000',
      GITHUB_SERVER_URL: 'https://github.com',
    } },
    fetch: async () => {
      modelCalls++;
      if (mutateHeadDuringReview) currentSha = 'd'.repeat(40);
      if (changedSourceField === 'base') currentBase = 'e'.repeat(40);
      if (changedSourceField === 'body') currentBody = 'Other task #3';
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
    requests, issueRequests };
}

function assertBlocked(result, explanation) {
  assert.equal(result.statuses.some(status => status.state === 'success'), false, explanation);
  assert.equal(result.statuses.at(-1)?.state, 'failure', 'Must report a terminal blocking outcome');
  assert.ok(result.failures.length > 0, 'Must explain the blocking outcome');
}

test('valid complete evidence still receives success on the tested head', async () => {
  const result = await simulateReview();
  assert.equal(result.statuses.at(-1)?.state, 'success');
  assert.equal(result.statuses.at(-1)?.sha, result.testedSha);
  assert.deepEqual(result.failures, []);
  assert.equal(result.modelCalls, 1);
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
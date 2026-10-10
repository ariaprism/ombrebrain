"""Exercise the real dashboard handlers with confirmation accepted/cancelled."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_dashboard_archive_and_delete_need_one_confirmation_only():
    html = (Path(__file__).resolve().parents[1] / "frontend/dashboard.html").read_text()
    markers = [
        ("async function bucketArchive(id)", "async function withdrawBucketDeletion"),
        ("async function runBucketBatch(action)", "async function hardDeleteSelectedTests"),
        ("async function reviewAction(bid, action)", "async function batchReview"),
        ("async function deleteLetter(id)", "async function withdrawLetterDeletion"),
    ]
    handlers = []
    for start, end in markers:
        i = html.index(start)
        handlers.append(html[i:html.index(end, i)])
    script = '''
const assert = require('node:assert/strict');
let accept = false, confirmations = 0, calls = [];
const BASE = '';
const selectedBucketIds = new Set(['memory']);
const window = {};
const document = {querySelectorAll: () => []};
function confirm() { confirmations++; return accept; }
function prompt() { throw new Error('unexpected reason prompt'); }
function alert() {}
function closeDetail() {}
async function loadBuckets() {}
async function loadLetters() {}
async function fetch(url, options) {
  calls.push({url, options});
  return {ok:true, json:async () => ({ok:true, deleted:true, applied:1, errors:0})};
}
async function authFetch(url, options) { return fetch(url, options); }
async function readJsonSafe(res) { return res.json(); }
''' + '\n'.join(handlers) + '''
(async () => {
  for (const handler of [() => bucketArchive('memory'), () => runBucketBatch('archive'),
                        () => reviewAction('memory', 'delete'), () => deleteLetter('letter')]) {
    accept = false; confirmations = 0; calls = [];
    selectedBucketIds.add('memory');
    await handler();
    assert.equal(confirmations, 1);
    assert.equal(calls.length, 0);
    accept = true; confirmations = 0;
    await handler();
    assert.equal(confirmations, 1);
    assert.equal(calls.length, 1);
    assert.equal(JSON.stringify(JSON.parse(calls[0].options.body)).includes('reason'), false);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr

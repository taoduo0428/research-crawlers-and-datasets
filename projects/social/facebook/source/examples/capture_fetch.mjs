#!/usr/bin/env node
/**
 * Safe Node 22+ example for the local Facebook capture API.
 * Public, dependency-free client. Connects only to a local loopback API;
 * does not access a Facebook account, browser profile, or external site.
 */
import {writeFile} from 'node:fs/promises';
import process from 'node:process';
import {performance} from 'node:perf_hooks';

function parseArgs(args) {
  const out = {
    baseUrl: 'http://127.0.0.1:8765',
    output: 'reactors.csv',
    timeout: 1800,
    url: '',
    jobId: '',
  };
  const keys = {
    '--base-url': 'baseUrl',
    '--output': 'output',
    '--timeout': 'timeout',
    '--url': 'url',
    '--job-id': 'jobId',
  };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help' || args[i] === '-h') {
      console.log('Usage: node examples/capture_fetch.mjs (--url URL | --job-id ID) [--base-url http://127.0.0.1:8765] [--output reactors.csv] [--timeout seconds]');
      process.exit(0);
    }
    const key = keys[args[i]];
    if (!key || !args[i + 1]) throw new Error('Unknown or incomplete option: ' + args[i]);
    out[key] = args[++i];
  }
  if (Boolean(out.url) === Boolean(out.jobId)) throw new Error('Provide exactly one --url or --job-id');
  out.timeout = Number(out.timeout);
  if (!Number.isFinite(out.timeout) || out.timeout <= 0) throw new Error('--timeout must be finite and positive');
  const base = new URL(out.baseUrl);
  if (base.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)
      || base.username || base.password || base.search || base.hash
      || (base.pathname !== '/' && base.pathname !== '')) {
    throw new Error('--base-url must be an unauthenticated HTTP loopback origin');
  }
  out.baseUrl = base.origin;
  if (out.jobId && !/^[-A-Za-z0-9_]{1,128}$/.test(out.jobId)) {
    throw new Error('Invalid job id');
  }
  return out;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  async function request(method, path, jsonBody) {
    const response = await fetch(args.baseUrl + path, {
      method,
      redirect: 'manual',
      headers: jsonBody === undefined ? {} : {'Content-Type': 'application/json'},
      body: jsonBody === undefined ? undefined : JSON.stringify(jsonBody),
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new Error('Local API HTTP ' + response.status + ': ' + (await response.text()).slice(0, 500));
    return response;
  }

  await request('GET', '/health');
  const jobId = args.jobId || (await (await request('POST', '/v1/jobs', {
    post_url: args.url,
    collect: ['reactions'],
    save_artifacts: ['progress', 'safe_capture'],
  })).json()).job_id;
  if (!/^[-A-Za-z0-9_]{1,128}$/.test(jobId || '')) throw new Error('Local API returned an invalid job ID');
  console.log('job_id=' + jobId);
  const path = '/v1/jobs/' + jobId;
  const deadline = performance.now() + args.timeout * 1000;

  while (true) {
    const job = await (await request('GET', path)).json();
    const status = job.status;
    if (status === 'completed') break;
    if (status === 'failed' || status === 'cancelled') throw new Error(JSON.stringify(job));
    if (!['queued', 'running', 'cancelling'].includes(status)) throw new Error('Unknown status: ' + status);
    const progress = await (await request('GET', path + '/progress')).json();
    console.log(JSON.stringify(progress));
    const remaining = deadline - performance.now();
    if (remaining <= 0) throw new Error('Timeout; resume with --job-id ' + jobId + '. Job not cancelled.');
    await sleep(Math.min(2000, remaining));
  }

  const result = await (await request('GET', path + '/result')).json();
  const bytes = Buffer.from(await (await request('GET', path + '/artifacts/facebook-reactor-users.csv?download=1')).arrayBuffer());
  await writeFile(args.output, bytes);
  console.log(JSON.stringify(result, null, 2));
}

main().catch(error => {
  console.error('Capture example failed:', error?.message || String(error));
  process.exitCode = 1;
});

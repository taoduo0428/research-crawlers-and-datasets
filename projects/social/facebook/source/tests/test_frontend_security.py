import unittest
import shutil
import subprocess
from pathlib import Path


class FrontendSecurityTests(unittest.TestCase):
    def test_v2_explains_serial_active_worker_and_standby(self):
        html = (Path(__file__).resolve().parents[1] / "web_v2" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("多链接并发", html)
        self.assertNotIn("批量并发测试", html)
        self.assertIn("单 active Worker 串行执行", html)
        self.assertIn("备用 Worker 仅用于故障接管", html)
        self.assertIn('id="postUrlsInput"', html)
        self.assertIn("02 / SWARM MATRIX", html)

    def test_dynamic_artifact_and_status_values_do_not_use_inner_html(self):
        source = (Path(__file__).resolve().parents[1] / "web_v2" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("tdName.innerHTML", source)
        self.assertNotIn("tdStatus.innerHTML", source)
        self.assertNotIn("tdPath.innerHTML", source)
        self.assertIn("nameCode.textContent", source)
        self.assertIn("pathCode.textContent", source)
        self.assertIn("statusPill.textContent", source)


    def test_artifact_values_are_literal_text_in_executed_renderer(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required for deterministic DOM fixture")
        script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set innerHTML(value) { throw new Error('dynamic HTML interpretation: ' + value); }
}
const tbody = new Element('tbody');
const payload = '<img src=x onerror="unexpected()">';
const source = fs.readFileSync('web_v2/app.js', 'utf8');
const renderer = source.slice(source.indexOf('function renderArtifactsVault('),
                             source.indexOf('// Render Batch Swarm Matrix'));
assert(renderer.startsWith('function renderArtifactsVault('));
const context = {document:{createElement: tag => new Element(tag)},
  $: () => tbody, state:{selectedJobId:'dummy'}, formatBytes: n => String(n), copyText: () => {}};
vm.createContext(context);
vm.runInContext(renderer, context);
for (const state of ['ready', payload]) {
  context.renderArtifactsVault([{name:payload, label:payload, path:payload,
    state, size_bytes:1, viewable:true, required:true, url:'/v1/jobs/dummy/artifacts/report.json'}]);
  const cells = tbody.children[0].children;
  assert.equal(cells[1].children[0].textContent, payload);
  assert.equal(cells[1].children[1].textContent, payload + ' · 必留');
  assert.equal(cells[4].children[0].textContent, payload);
  assert.equal(cells[4].children[0].title, payload);
  assert.equal(cells[2].children[0].textContent, state === 'ready' ? '已落盘' : payload);
  if (state === 'ready') {
    const links = cells[5].children.filter(e => e.tag === 'a');
    assert.equal(links.length, 2);
    assert.equal(links[0].href, '/v1/jobs/dummy/artifacts/report.json');
    assert.equal(links[1].href, '/v1/jobs/dummy/artifacts/report.json?download=1');
  }
}
console.log('PASS literal artifact values and download/preview links');
"""
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, "-e", script], cwd=root, capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS literal artifact values", result.stdout)


if __name__ == "__main__":
    unittest.main()

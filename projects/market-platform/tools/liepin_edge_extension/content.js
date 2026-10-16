"use strict";

(() => {
  if (globalThis.__liepinEdgeBridgeContentLoaded) {
    return;
  }
  globalThis.__liepinEdgeBridgeContentLoaded = true;

  const JD_SELECTORS = [
    'dd[data-selector="job-intro-content"]',
    '[data-selector="job-intro-content"]',
    ".job-description-main",
    ".job-intro-content",
    ".job-intro-container dd"
  ];
  const TITLE_SELECTORS = [
    '[data-selector="job-title"]',
    ".job-info-box h1",
    ".job-title-box h1",
    ".job-title",
    "h1"
  ];
  let started = false;
  let submitted = false;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function cleanLines(value) {
    const lines = String(value || "")
      .replace(/\r/g, "")
      .split("\n")
      .map((line) => line.replace(/[\t\f\v ]+/g, " ").trim())
      .filter(Boolean);
    return lines.filter((line, index) => index === 0 || line !== lines[index - 1]).join("\n");
  }

  function extractTitle() {
    for (const selector of TITLE_SELECTORS) {
      const element = document.querySelector(selector);
      const value = cleanLines(element && element.innerText);
      if (value) {
        return value.split("\n", 1)[0];
      }
    }
    return "";
  }

  function findDescriptionElement() {
    for (const selector of JD_SELECTORS) {
      const element = document.querySelector(selector);
      if (element && cleanLines(element.innerText)) {
        return element;
      }
    }
    for (const paragraph of document.querySelectorAll("dl.paragraph")) {
      const heading = cleanLines(paragraph.querySelector("dt")?.innerText);
      if (/^(职位介绍|职位描述|岗位职责|工作内容)$/.test(heading)) {
        const element = paragraph.querySelector("dd");
        if (element && cleanLines(element.innerText)) {
          return element;
        }
      }
    }
    return null;
  }

  function pageText(limit = 12_000) {
    return cleanLines(document.body?.innerText || "").slice(0, limit);
  }

  function classifyGate() {
    const hostname = location.hostname.toLowerCase();
    if (hostname === "wow.liepin.com" || hostname === "safe.liepin.com") {
      return "security_transit";
    }
    if (hostname === "passport.liepin.com") {
      return "login_required";
    }
    const title = document.title || "";
    const text = pageText();
    if (
      document.querySelector(
        '[class*="geetest"], [id*="geetest"], [class*="captcha"], ' +
        '[id*="captcha"], iframe[src*="captcha"], iframe[src*="verify"]'
      ) ||
      /安全验证|人机验证|访问异常/.test(title) ||
      (text.length < 5_000 && /请完成(?:安全|人机)验证|请拖动滑块|访问存在异常/.test(text))
    ) {
      return "security_transit";
    }
    if (
      /登录失效|请重新登录|登录后查看/.test(title) ||
      (text.length < 5_000 && /登录失效|请重新登录|请先登录|登录后查看/.test(text))
    ) {
      return "login_required";
    }
    return null;
  }

  function isUnavailable() {
    const text = pageText();
    return text.length < 8_000 && (
      /职位已(?:下线|关闭|过期|暂停招聘)/.test(text) ||
      /该职位(?:已关闭|不存在|已下线)/.test(text) ||
      /停止招聘|暂无该职位|页面不存在/.test(text)
    );
  }

  async function outcomeWithinDeadline() {
    const deadline = Date.now() + 5_000;
    while (Date.now() <= deadline) {
      const gate = classifyGate();
      if (gate) {
        return { status: gate, jd: "" };
      }
      const element = findDescriptionElement();
      if (element) {
        const jd = cleanLines(element.innerText);
        if (jd) {
          return { status: "success", jd };
        }
      }
      if (isUnavailable()) {
        return { status: "unavailable", jd: "" };
      }
      await sleep(150);
    }
    const gate = classifyGate();
    if (gate) {
      return { status: gate, jd: "" };
    }
    if (isUnavailable()) {
      return { status: "unavailable", jd: "" };
    }
    return { status: "empty", jd: "" };
  }

  function sendMessage(message) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(message, (response) => {
        const error = chrome.runtime.lastError;
        if (error) {
          reject(new Error(error.message));
          return;
        }
        if (!response || response.accepted !== true) {
          reject(new Error(response?.reason || "background rejected result"));
          return;
        }
        resolve();
      });
    });
  }

  async function submit(outcome) {
    if (submitted) {
      return;
    }
    submitted = true;
    const message = {
      type: "liepin_detail_result",
      status: outcome.status,
      jd: outcome.jd,
      final_url: location.href,
      title: extractTitle()
    };
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await sendMessage(message);
        return;
      } catch (error) {
        if (attempt === 3) {
          console.error("Liepin JD result could not be submitted", error);
          return;
        }
        await sleep(250 * attempt);
      }
    }
  }

  async function run() {
    if (started) {
      return;
    }
    started = true;
    try {
      await submit(await outcomeWithinDeadline());
    } catch (error) {
      await submit({ status: "error", jd: "" });
      console.error("Liepin JD extraction failed", error);
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === "liepin_extract_now") {
      void run();
    }
  });

  void run();
})();

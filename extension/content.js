/* ==========================================================================
   AI Studio Vibe Bridge - content.js
   يربط واجهة Google AI Studio بخادم البايثون المحلي (Local Bridge) عبر
   بروتوكول وسوم (Tagging Protocol) توضع في السطر الذي يسبق كتل الكود:

       [NEW: path]      -> إنشاء ملف جديد بالكامل (full)
       [UPDATE: path]   -> كتابة/استبدال ملف موجود بالكامل (full)
       [FILE: path]     -> مرادف لـ UPDATE (full)
       [FIND: path]     -> كتلة تحتوي الجزء المطلوب البحث عنه
       [REPLACE: path]  -> كتلة تحتوي الكود البديل لكتلة FIND السابقة

   المكوّنات:
     - مؤشر حالة عائم (Status Pill).
     - أزرار محقونة داخل ترويسة كل كتلة كود (Per-Block Buttons).
     - شريط سفلي مدمج: Digest Codebase + Push All (Latest Response).
   ========================================================================== */

(function () {
  "use strict";

  // -------------------------------------------------------------------------
  // الثوابت
  // -------------------------------------------------------------------------

  const BRIDGE_URL = "http://127.0.0.1:5000";
  const HEALTH_INTERVAL_MS = 10000;

  // -------------------------------------------------------------------------
  // سجلات التتبع (Console Logging) - للتشخيص
  // -------------------------------------------------------------------------

  const LOG_PREFIX = "[Vibe Bridge]";

  function log(...args) {
    console.log(LOG_PREFIX, ...args);
  }
  function logWarn(...args) {
    console.warn(LOG_PREFIX, ...args);
  }
  function logErr(...args) {
    console.error(LOG_PREFIX, ...args);
  }

  // محددات محتملة لصندوق الكتابة في AI Studio (نُجرّبها بالترتيب).
  const INPUT_SELECTORS = [
    'rich-textarea div[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'textarea[aria-label]',
    "rich-textarea textarea",
    'div[contenteditable="true"]',
    "textarea",
  ];

  // محددات "أدوار" المحادثة لتحديد آخر رد للذكاء الاصطناعي.
  const TURN_SELECTORS = [
    'ms-chat-turn[data-turn-role="assistant"]',
    "ms-chat-turn.model",
    '[data-turn-role="assistant"]',
    "ms-chat-turn",
  ];

  // نمط وسوم البروتوكول.
  const TAG_RE = /\[(NEW|UPDATE|FILE|FIND|REPLACE)\s*:\s*([^\]\n]+)\]/gi;

  // -------------------------------------------------------------------------
  // أدوات مساعدة عامة
  // -------------------------------------------------------------------------

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function isVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden"
    );
  }

  // -------------------------------------------------------------------------
  // الإشعارات (Toast)
  // -------------------------------------------------------------------------

  function getToastContainer() {
    let container = document.getElementById("vibe-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "vibe-toast-container";
      document.body.appendChild(container);
    }
    return container;
  }

  function showToast(message, type = "info", duration = 3500) {
    const container = getToastContainer();
    const toast = document.createElement("div");
    toast.className = `vibe-toast vibe-toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add("vibe-toast-hide");
      setTimeout(() => toast.remove(), 260);
    }, duration);
  }

  // -------------------------------------------------------------------------
  // مؤشر الحالة (Status Pill)
  // -------------------------------------------------------------------------

  let statusDot;

  function createStatusPill() {
    if (document.getElementById("vibe-status-pill")) return;
    const pill = document.createElement("div");
    pill.id = "vibe-status-pill";
    pill.innerHTML = `
      <span id="vibe-dot" class="vibe-dot"></span>
      <span id="vibe-status-text">جارٍ الفحص…</span>
    `;
    document.body.appendChild(pill);
    statusDot = document.getElementById("vibe-dot");
  }

  function setStatus(isOnline) {
    if (!statusDot) statusDot = document.getElementById("vibe-dot");
    if (!statusDot) return;
    statusDot.classList.toggle("vibe-online", isOnline);
    statusDot.classList.toggle("vibe-offline", !isOnline);
    const text = document.getElementById("vibe-status-text");
    if (text) text.textContent = isOnline ? "متصل" : "غير متصل";
  }

  async function checkHealth() {
    log("فحص الحالة: GET /health …");
    try {
      const res = await bridgeFetch("/health", { method: "GET" });
      const online = !!res.ok;
      setStatus(online);
      log(online ? "🟢 الخادم متصل" : `🔴 الخادم غير متصل (status=${res.status})`);
      return online;
    } catch (err) {
      setStatus(false);
      logErr("🔴 فشل فحص الاتصال بالخادم:", err.message);
      return false;
    }
  }

  // -------------------------------------------------------------------------
  // أدوات كتل الأكواد
  // -------------------------------------------------------------------------

  /** جلب كتل الأكواد (ms-code-block أو pre) مع تجنّب التكرار المتداخل. */
  function getCodeBlocks(root = document) {
    const blocks = [];
    const nodes = root.querySelectorAll("ms-code-block, pre");
    nodes.forEach((el) => {
      // تجاهل <pre> المتداخل داخل ms-code-block لتجنّب تكرار نفس الكتلة.
      if (el.tagName === "PRE" && el.closest("ms-code-block")) return;
      blocks.push(el);
    });
    return blocks;
  }

  /**
   * توحيد نص الكود المستخرج قبل إرساله للخادم:
   *   - تحويل نهايات الأسطر (CRLF/CR) إلى LF.
   *   - تحويل المسافات غير الفاصلة (\u00a0) إلى مسافات عادية.
   * هذا يقلّل فوارق التطابق بين نص المتصفح ومحتوى الملف على القرص.
   */
  function normalizeCodeText(raw) {
    return (raw || "")
      .replace(/\r\n?/g, "\n")
      .replace(/\u00a0/g, " ");
  }

  /** استخراج النص البرمجي الفعلي (يتعامل مع shadowRoot إن وُجد). */
  function getBlockCodeText(el) {
    const root = el.shadowRoot || el;
    const pre = root.querySelector("pre");
    const code = root.querySelector("code");
    const target = pre || code || root;
    return normalizeCodeText(target.innerText || target.textContent || "");
  }

  /** استخراج آخر وسم موجود في نص ما. */
  function extractTag(text) {
    if (!text) return null;
    TAG_RE.lastIndex = 0;
    let match;
    let last = null;
    while ((match = TAG_RE.exec(text)) !== null) {
      last = { type: match[1].toUpperCase(), path: match[2].trim() };
    }
    return last;
  }

  /**
   * جمع النص الذي يسبق كتلة كود مباشرةً في ترتيب المستند، مع إعادة التصفير
   * عند كل كتلة كود سابقة (بحيث نحصل على نص ما بعد آخر كتلة كود فقط).
   */
  function precedingTextForBlock(block) {
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
      null
    );
    let buffer = "";
    let node;
    while ((node = walker.nextNode())) {
      if (block.contains(node)) break; // وصلنا لبداية الكتلة الحالية

      const owner = node.parentElement
        ? node.parentElement.closest("ms-code-block, pre")
        : null;

      if (owner && owner !== block) {
        buffer = ""; // نبدأ من جديد بعد كل كتلة كود سابقة
      } else {
        buffer += node.nodeValue + "\n";
      }
    }
    return buffer;
  }

  /** استخراج الوسم الخاص بكتلة كود معيّنة. */
  function tagForBlock(block) {
    return extractTag(precedingTextForBlock(block));
  }

  // -------------------------------------------------------------------------
  // استدعاءات الخادم (Bridge API)
  //
  // ملاحظة مهمة: لا نتصل بالخادم المحلي مباشرة من content.js لأن سياسة
  // Chrome (Private Network Access) تحجب ذلك بـ 403. بدلاً من ذلك نمرّر
  // الطلب إلى background.js (Service Worker) المعفى من قيود CORS/PNA.
  // -------------------------------------------------------------------------

  /**
   * تنفيذ طلب إلى الجسر عبر الـ Service Worker.
   * @returns {Promise<{ok:boolean,status:number,data:any,error?:string}>}
   */
  function bridgeFetch(path, options = {}) {
    const url = `${BRIDGE_URL}${path}`;
    log("→ إرسال طلب عبر background.js:", (options.method || "GET"), url);
    return new Promise((resolve, reject) => {
      // التأكد من توفّر واجهة الإضافات (يظهر الخطأ إن لم تُحمَّل الإضافة).
      if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) {
        const msg =
          "chrome.runtime غير متاح — تأكد من تحميل/Reload الإضافة في chrome://extensions";
        logErr(msg);
        reject(new Error(msg));
        return;
      }

      try {
        chrome.runtime.sendMessage(
          { type: "FETCH_BRIDGE", url, options },
          (response) => {
            if (chrome.runtime.lastError) {
              const msg = `sendMessage فشل: ${chrome.runtime.lastError.message}`;
              logErr(msg);
              reject(new Error(msg));
              return;
            }
            if (!response) {
              const msg = "لا يوجد رد من background.js (هل الـ Service Worker مسجّل؟)";
              logErr(msg);
              reject(new Error(msg));
              return;
            }
            if (response.ok) {
              log("← رد ناجح:", response.status, url, response.data);
            } else {
              logErr("← رد فاشل:", response.status, url, response.error || "");
            }
            resolve(response);
          }
        );
      } catch (err) {
        logErr("استثناء أثناء sendMessage:", err);
        reject(err);
      }
    });
  }

  /** كتابة/استبدال ملف بالكامل. */
  async function pushFull(path, content) {
    const res = await bridgeFetch("/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "full", path, content }),
    });
    if (!res.ok) throw new Error(res.error || `HTTP ${res.status}`);
    return res.data;
  }

  /** هل تم التطبيق عبر المطابقة الضبابية الاحتياطية؟ */
  function isFuzzyStrategy(strategy) {
    return typeof strategy === "string" && strategy.startsWith("fuzzy");
  }

  /** بحث واستبدال مباشر داخل ملف. */
  async function pushReplace(path, find, replace) {
    const res = await bridgeFetch("/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "replace", path, find, replace }),
    });
    if (!res.ok) throw new Error(res.error || `HTTP ${res.status}`);
    return res.data;
  }

  // -------------------------------------------------------------------------
  // أزرار كتل الأكواد (Per-Block Buttons)
  // -------------------------------------------------------------------------

  const BLOCK_META = new WeakMap(); // el -> { type, path }

  /** إزالة ترويسة الأزرار المضافة إن وُجدت. */
  function removeToolbar(el) {
    if (el.__vibeToolbar && el.__vibeToolbar.parentElement) {
      el.__vibeToolbar.remove();
    }
    el.__vibeToolbar = null;
  }

  /** إنشاء ترويسة أزرار جديدة لكتلة معيّنة. */
  function createToolbar(el, tag) {
    const toolbar = document.createElement("div");
    toolbar.className = "vibe-block-toolbar";
    toolbar.setAttribute("data-vibe", "1");

    if (tag.type === "FIND") {
      // بادج قراءة فقط.
      const badge = document.createElement("span");
      badge.className = "vibe-badge-find";
      badge.textContent = "🔍 Target: FIND";
      toolbar.appendChild(badge);
    } else if (tag.type === "REPLACE") {
      // زر أزرق لتطبيق الاستبدال مقترناً بكتلة FIND السابقة.
      const btn = document.createElement("button");
      btn.className = "vibe-code-btn vibe-code-btn-blue";
      btn.textContent = "Apply Replace";
      btn.addEventListener("click", async () => {
        const findBlock = findPrecedingFindBlock(el, tag.path);
        if (!findBlock) {
          showToast(`لا توجد كتلة FIND سابقة للملف: ${tag.path}`, "error");
          return;
        }
        btn.disabled = true;
        try {
          const findText = getBlockCodeText(findBlock);
          const replaceText = getBlockCodeText(el);
          const data = await pushReplace(tag.path, findText, replaceText);
          if (isFuzzyStrategy(data && data.strategy)) {
            showToast(
              `تم الاستبدال بمطابقة ضبابية (${data.strategy}) — راجع الملف: ${tag.path}`,
              "info",
              6000
            );
          } else {
            showToast(`تم تطبيق الاستبدال: ${tag.path}`, "success");
          }
        } catch (err) {
          showToast(`فشل الاستبدال (${tag.path}): ${err.message}`, "error", 5000);
        } finally {
          btn.disabled = false;
        }
      });
      toolbar.appendChild(btn);
    } else {
      // NEW / UPDATE / FILE -> زر أخضر لدفع الملف كاملاً.
      const isNew = tag.type === "NEW";
      const btn = document.createElement("button");
      btn.className = "vibe-code-btn vibe-code-btn-green";
      btn.textContent = isNew ? "🚀 Push New File" : "🚀 Push File";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await pushFull(tag.path, getBlockCodeText(el));
          showToast(`تم دفع الملف: ${tag.path}`, "success");
        } catch (err) {
          showToast(`فشل الدفع (${tag.path}): ${err.message}`, "error", 5000);
        } finally {
          btn.disabled = false;
        }
      });
      toolbar.appendChild(btn);
    }

    el.__vibeToolbar = toolbar;
    // نحقن الترويسة قبل الكتلة في الـ light DOM ليُطبَّق عليها styles.css.
    if (el.parentElement) {
      el.parentElement.insertBefore(toolbar, el);
    }
  }

  /** إيجاد كتلة FIND السابقة لنفس المسار (الأقرب قبل الكتلة الحالية). */
  function findPrecedingFindBlock(currentEl, path) {
    const blocks = getCodeBlocks(document);
    const index = blocks.indexOf(currentEl);
    for (let i = index - 1; i >= 0; i--) {
      const meta = BLOCK_META.get(blocks[i]) || tagForBlock(blocks[i]);
      if (meta && meta.type === "FIND" && meta.path === path) return blocks[i];
    }
    return null;
  }

  /** فحص كل كتل الأكواد وحقن/تحديث الأزرار حسب الوسوم. */
  function scanCodeBlocks() {
    const blocks = getCodeBlocks(document);
    for (const el of blocks) {
      const tag = tagForBlock(el);
      const prev = BLOCK_META.get(el);

      if (tag) {
        BLOCK_META.set(el, tag);
        const same =
          prev && prev.type === tag.type && prev.path === tag.path;
        // إعادة الإنشاء فقط عند التغيير أو غياب الترويسة.
        if (!same || !el.__vibeToolbar || !el.__vibeToolbar.isConnected) {
          removeToolbar(el);
          createToolbar(el, tag);
        }
      } else if (el.__vibeToolbar) {
        removeToolbar(el);
        BLOCK_META.delete(el);
      }
    }
  }

  // -------------------------------------------------------------------------
  // الشريط السفلي المدمج (Bottom Action Bar)
  // -------------------------------------------------------------------------

  /** إيجاد صندوق الكتابة المرئي في AI Studio. */
  function findInputBox() {
    for (const selector of INPUT_SELECTORS) {
      const candidates = Array.from(document.querySelectorAll(selector)).filter(
        isVisible
      );
      if (candidates.length > 0) {
        return candidates.sort(
          (a, b) =>
            b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom
        )[0];
      }
    }
    return null;
  }

  function createBottomBar() {
    const bar = document.createElement("div");
    bar.id = "vibe-bottom-bar";
    bar.setAttribute("data-vibe", "1");
    bar.innerHTML = `
      <button id="vibe-digest" class="vibe-bar-btn vibe-bar-digest">📁 Digest Codebase</button>
      <button id="vibe-pushall" class="vibe-bar-btn vibe-bar-pushall">🚀 Push All (Latest Response)</button>
    `;
    bar
      .querySelector("#vibe-digest")
      .addEventListener("click", onDigestCodebase);
    bar.querySelector("#vibe-pushall").addEventListener("click", onPushAll);
    return bar;
  }

  /**
   * تثبيت الزرّين في الجهة اليمنى داخل صف أزرار صندوق الكتابة
   * (prompt-box-container). يُعاد تقييم المكان في كل استدعاء: فإن كان الشريط
   * عائماً (fallback) بينما صندوق الكتابة أصبح متاحاً، يُنقل إليه تلقائياً.
   * وعند غياب صندوق الكتابة نُبقيه شريطاً عائماً في الزاوية السفلية اليسرى.
   */
  function mountBottomBar() {
    // تحديد الوجهة المطلوبة الآن.
    const rightGroup = document.querySelector(
      ".prompt-box-container .buttons-row .button-wrapper"
    );
    const buttonsRow = document.querySelector(
      ".prompt-box-container .buttons-row"
    );

    const target = rightGroup || buttonsRow || document.body;
    const mode = rightGroup || buttonsRow ? "inline" : "fixed";

    let bar = document.getElementById("vibe-bottom-bar");

    // إن كان الشريط موجوداً في المكان الصحيح بالفعل، لا نلمسه.
    if (bar) {
      const inPlace =
        (mode === "inline" && bar.parentElement === target) ||
        (mode === "fixed" && bar.parentElement === document.body);
      if (inPlace) return;

      bar.remove(); // موجود في مكان خاطئ (مثلاً عائم) → أعد بناءه بالمكان الصحيح.
      bar = null;
    }

    bar = createBottomBar();
    bar.classList.remove("vibe-bar-inline", "vibe-bar-fixed");

    if (mode === "inline") {
      bar.classList.add("vibe-bar-inline");
      if (rightGroup) {
        // قبل زر المايك/التشغيل داخل المجموعة اليمنى.
        rightGroup.insertBefore(bar, rightGroup.firstChild);
      } else {
        buttonsRow.appendChild(bar);
      }
    } else {
      bar.classList.add("vibe-bar-fixed");
      document.body.appendChild(bar);
    }
  }

  // -------------------------------------------------------------------------
  // Digest Codebase (سياق المشروع)
  // -------------------------------------------------------------------------

  function formatContext(files) {
    let out = "Here is the current project context and files:\n\n";
    for (const file of files) {
      out += `=== FILE: ${file.path} ===\n${file.content}\n\n`;
    }
    out +=
      "=== END OF CONTEXT ===\n" +
      "Instructions for changes (use tags on the line BEFORE each code block):\n" +
      "- To create a new file: put [NEW: relative/path] immediately before the code block.\n" +
      "- To rewrite an existing file completely: use [UPDATE: relative/path] " +
      "(or [FILE: relative/path]) before the code block.\n" +
      "- For partial edits: output a [FIND: relative/path] code block with the exact " +
      "existing snippet, followed by a [REPLACE: relative/path] code block with the " +
      "replacement code.\n" +
      "- Always use relative paths from the project root.";
    return out;
  }

  async function injectIntoInput(text) {
    const input = findInputBox();
    if (!input) return false;

    input.focus();
    await sleep(50);

    try {
      if (input.tagName === "TEXTAREA") {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          "value"
        ).set;
        setter.call(input, text);
      } else {
        input.textContent = "";
        const selection = window.getSelection();
        selection.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(input);
        selection.addRange(range);
        const inserted = document.execCommand("insertText", false, text);
        if (!inserted) input.textContent = text;
      }
    } catch (err) {
      if (input.tagName === "TEXTAREA") input.value = text;
      else input.textContent = text;
    }

    input.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: text,
      })
    );
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      return false;
    }
  }

  async function onDigestCodebase() {
    const btn = document.getElementById("vibe-digest");
    btn.disabled = true;
    log("Digest Codebase: بدء الجلب…");
    try {
      const res = await bridgeFetch("/context", { method: "GET" });
      if (!res.ok) throw new Error(res.error || `HTTP ${res.status}`);
      const data = res.data || {};
      const files = data.files || [];
      const text = formatContext(files);

      const injected = await injectIntoInput(text);
      if (injected) {
        showToast(`تم جلب السياق (${files.length} ملفات)`, "success");
      } else if (await copyToClipboard(text)) {
        showToast(
          `تعذّر الحقن — تم نسخ السياق (${files.length} ملفات) للحافظة`,
          "info",
          4500
        );
      } else {
        showToast("تعذّر حقن السياق أو نسخه", "error");
      }
    } catch (err) {
      showToast(`خطأ في جلب السياق: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  }

  // -------------------------------------------------------------------------
  // Push All (Latest Response)
  // -------------------------------------------------------------------------

  /** إرجاع نطاق آخر رد للذكاء الاصطناعي (أو المستند كاملاً عند التعذّر). */
  function getLastResponseScope() {
    for (const selector of TURN_SELECTORS) {
      const turns = Array.from(document.querySelectorAll(selector));
      if (turns.length > 0) return turns[turns.length - 1];
    }
    return document;
  }

  /** جمع كل الكتل الموسومة داخل نطاق معيّن بترتيب المستند. */
  function collectTaggedBlocks(scope) {
    const tagged = [];
    for (const el of getCodeBlocks(scope)) {
      const tag = BLOCK_META.get(el) || tagForBlock(el);
      if (tag) tagged.push({ el, tag, text: getBlockCodeText(el) });
    }
    return tagged;
  }

  async function onPushAll() {
    const btn = document.getElementById("vibe-pushall");
    btn.disabled = true;
    log("Push All: بدء المعالجة…");
    try {
      const scope = getLastResponseScope();
      const tagged = collectTaggedBlocks(scope);

      if (tagged.length === 0) {
        showToast("لم يتم العثور على أي وسوم في آخر رد", "info", 4000);
        return;
      }

      let success = 0;
      let replaceCount = 0;
      let fuzzyCount = 0;
      const errors = [];
      const findByPath = {}; // آخر كتلة FIND لكل مسار

      for (const item of tagged) {
        const { type, path } = item.tag;
        try {
          if (type === "FIND") {
            findByPath[path] = item.text; // تسجيل للاقتران مع REPLACE لاحقاً
          } else if (type === "REPLACE") {
            const findText = findByPath[path];
            if (findText == null) {
              errors.push(`${path}: لا توجد كتلة FIND مقابلة`);
              continue;
            }
            const data = await pushReplace(path, findText, item.text);
            if (isFuzzyStrategy(data && data.strategy)) fuzzyCount += 1;
            success += 1;
            replaceCount += 1;
          } else {
            // NEW / UPDATE / FILE
            await pushFull(path, item.text);
            success += 1;
          }
        } catch (err) {
          errors.push(`${path}: ${err.message}`);
        }
      }

      if (success > 0) {
        const fuzzyNote =
          fuzzyCount > 0
            ? ` — منها ${fuzzyCount} بمطابقة ضبابية (يُنصح بالمراجعة)`
            : "";
        showToast(
          `تم تحديث ${success} ملفات بنجاح (منها ${replaceCount} استبدال)${fuzzyNote}`,
          fuzzyCount > 0 ? "info" : "success",
          6000
        );
      }
      if (errors.length > 0) {
        showToast(`أخطاء (${errors.length}): ${errors.join(" | ")}`, "error", 7000);
      }
    } catch (err) {
      showToast(`خطأ أثناء Push All: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  }

  // -------------------------------------------------------------------------
  // المراقبة (MutationObserver) والتهيئة
  // -------------------------------------------------------------------------

  let scanScheduled = false;
  function scheduleScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    // تأجيل خفيف لتجميع التغييرات المتلاحقة (debounce).
    setTimeout(() => {
      scanScheduled = false;
      try {
        scanCodeBlocks();
        mountBottomBar();
      } catch (err) {
        /* تجاهل أخطاء الفحص المؤقتة أثناء إعادة رسم الصفحة */
      }
    }, 400);
  }

  function startObserver() {
    const observer = new MutationObserver((mutations) => {
      // نتجاهل التغييرات التي سببّتها عناصرنا الخاصة فقط.
      const onlyOurs = mutations.every((m) => {
        const nodes = [...m.addedNodes, ...m.removedNodes];
        return nodes.every(
          (n) =>
            n.nodeType === Node.ELEMENT_NODE &&
            n.hasAttribute &&
            n.hasAttribute("data-vibe")
        );
      });
      if (onlyOurs && mutations.length > 0) return;
      scheduleScan();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    log("تهيئة الإضافة…", {
      bridge: BRIDGE_URL,
      runtimeId: typeof chrome !== "undefined" && chrome.runtime ? chrome.runtime.id : "غير متاح",
      url: location.href,
    });
    createStatusPill();
    mountBottomBar();
    scanCodeBlocks();
    checkHealth();
    setInterval(checkHealth, HEALTH_INTERVAL_MS);
    setInterval(() => {
      mountBottomBar();
      scanCodeBlocks();
    }, 5000);
    startObserver();
    log("اكتملت التهيئة ✅");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

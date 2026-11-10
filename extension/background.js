/* ==========================================================================
   AI Studio Vibe Bridge - background.js (Service Worker)
   يعمل كوسيط (Proxy) بين content.js والخادم المحلي bridge.py.

   السبب: سياسة Chrome الصارمة Private Network Access (PNA) تمنع صفحة
   https://aistudio.google.com من الاتصال المباشر بـ http://127.0.0.1
   فترد بـ 403 Forbidden. الـ Service Worker معفى من قيود CORS/PNA، فنتولى
   منه تنفيذ الطلب ثم نمرّر النتيجة إلى content.js.
   ========================================================================== */

// النطاقات المحلية المسموح بمراسلتها فقط (حماية إضافية).
const ALLOWED_PREFIXES = [
  "http://127.0.0.1:5000",
  "http://localhost:5000",
];

const LOG_PREFIX = "[Vibe Bridge:bg]";
function log(...args) {
  console.log(LOG_PREFIX, ...args);
}
function logErr(...args) {
  console.error(LOG_PREFIX, ...args);
}

/** هل الـ URL ضمن النطاقات المحلية المسموحة؟ */
function isAllowedUrl(url) {
  return (
    typeof url === "string" &&
    ALLOWED_PREFIXES.some((prefix) => url.startsWith(prefix))
  );
}

log("Service Worker تم تحميله ✅");

// الاستماع للرسائل القادمة من content.js.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "FETCH_BRIDGE") {
    return; // ليس طلبنا؛ اتركه لمعالجات أخرى.
  }

  const { url, options } = message;
  log("استلمت طلب FETCH_BRIDGE:", (options && options.method) || "GET", url);

  if (!isAllowedUrl(url)) {
    const error = `عنوان غير مسموح به: ${url}`;
    logErr(error);
    sendResponse({ ok: false, status: 0, error });
    return false;
  }

  // تنفيذ الطلب الفعلي بصلاحيات الـ Service Worker المعفاة من CORS.
  fetch(url, options || {})
    .then(async (res) => {
      let data = null;
      try {
        data = await res.json();
      } catch (err) {
        data = null; // استجابة ليست JSON (مثل 204).
      }
      log("رد الخادم:", res.status, url, data);
      sendResponse({ ok: res.ok, status: res.status, data });
    })
    .catch((err) => {
      // الخادم مغلق أو تعذّر الوصول.
      const error = err && err.message ? err.message : String(err);
      logErr(
        "فشل fetch (هل bridge.py يعمل على المنفذ 5000؟):",
        error,
        url
      );
      sendResponse({ ok: false, status: 0, error });
    });

  return true; // إبقاء القناة مفتوحة للرد غير المتزامن.
});

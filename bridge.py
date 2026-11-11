# -*- coding: utf-8 -*-
"""
Local Bridge Server for Vibe Coding
=====================================
خادم محلي بسيط مبني على Flask يعمل كجسر بين واجهة Vibe Coding ونظام الملفات
على الجهاز. يوفّر ثلاث نقاط أساسية:
    - GET  /health   : للتأكد من أن الخادم يعمل وإرجاع مجلد العمل الحالي وحدود المسح.
    - GET  /context  : لمسح مجلد العمل وإرجاع الملفات النصية ومحتوياتها، مع إحصاءات
                       الحجم وتقدير التوكن والملفات المستبعدة.
    - POST /write    : لكتابة/تعديل الملفات بنمطين صريحين عبر حقل mode:
                       "full" للكتابة الكاملة، و "replace" للبحث والاستبدال المباشر.

حدود المسح (يمكن ضبطها عبر متغيرات بيئة):
    VIBE_MAX_FILE_BYTES   : أكبر حجم لملف واحد يُرسل (الافتراضي 400KB).
    VIBE_MAX_TOTAL_BYTES  : أكبر حجم كلي لحمولة /context (الافتراضي 2MB).

التشغيل:
    pip install -r requirements.txt
    python bridge.py
"""

import difflib
import os
import re

from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# الإعداد الأساسي
# ---------------------------------------------------------------------------

app = Flask(__name__)

# تفعيل CORS لجميع النطاقات حتى تستطيع الواجهة (في المتصفح) الاتصال بالخادم المحلي.
CORS(app)


@app.after_request
def add_cors_headers(response):
    """
    إضافة ترويسات CORS الكاملة + ترويسة Private Network Access (PNA).

    Chrome يفرض سياسة PNA عند اتصال موقع إنترنت (aistudio.google.com) بجهاز
    محلي (127.0.0.1). إرسال Access-Control-Allow-Private-Network يسمح بذلك.
    (الإضافة تتم عبر الـ Service Worker في الإضافة، وهذه حماية إضافية للوصول المباشر.)
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def handle_preflight(path):
    """الرد على طلبات الـ Preflight (OPTIONS) فوراً بكود 200."""
    return ("", 200)

# ---------------------------------------------------------------------------
# إعدادات المسح (Context Scan Limits)
# ---------------------------------------------------------------------------


def _int_env(name, default):
    """قراءة متغير بيئة رقمي مع الرجوع إلى قيمة افتراضية آمنة."""
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# أكبر حجم لملف واحد يُرسل ضمن السياق (بالبايت). الملفات الأكبر تُستبعد ويُبلَّغ عنها.
MAX_FILE_BYTES = _int_env("VIBE_MAX_FILE_BYTES", 256 * 1024)
# السقف الكلي لحمولة /context. يمنع تجاوز نافذة السياق بملفات مولَّدة ضخمة.
# الافتراضي متحفظ لأن التقدير بالتوكن تقريبي، والنص العربي أثقل من الإنجليزي.
MAX_TOTAL_BYTES = _int_env("VIBE_MAX_TOTAL_BYTES", 1024 * 1024)
# تقدير تقريبي للتوكن: ~4 أحرف لكل توكن (كافٍ للقرار، وليس للفوترة).
CHARS_PER_TOKEN = 4
# أقصى عدد من الملفات المستبعدة يُبلَّغ عنه تفصيلاً (لتجنّب رد ضخم).
MAX_SKIP_REPORT = 40

# مجلدات لا تُمسح أبدًا (لا معنى لها في السياق، وبعضها ضخم جدًا).
ALWAYS_IGNORE_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "out",
    "coverage",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".parcel-cache",
    ".cache",
    ".wrangler",
    ".vercel",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".terraform",
    ".gradle",
    ".dart_tool",
    "Pods",
}

# أسماء ملفات مستبعدة دائمًا (مولَّدة أو مكرّرة أو بلا قيمة تخطيطية).
ALWAYS_IGNORE_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "poetry.lock",
    "codebase.md",
}

# لواحق ملفات مستبعدة دائمًا (خرائط وأكواد مضغوطة وأرشيفات ذاتية).
ALWAYS_IGNORE_FILE_SUFFIXES = (
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".ico",
    ".svgz",
)

# ملفات حساسة تُحجب حجبًا صلبًا: لا تُرسل إلى أي نموذج مهما كان .gitignore.
SENSITIVE_FILE_NAMES = {
    ".env",
    ".dev.vars",
    ".npmrc",
    ".netrc",
    ".pgpass",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "credentials.json",
}
SENSITIVE_FILE_PREFIXES = (".env.",)
SENSITIVE_FILE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".asc",
)
# استثناءات آمنة: قوالب لا تحتوي أسرارًا فعلية.
SENSITIVE_ALLOWLIST = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
}


# ---------------------------------------------------------------------------
# مطابق مبسّط لأنماط .gitignore (بدون اعتماديات خارجية)
# ---------------------------------------------------------------------------


def _gitignore_glob_to_regex(pattern, anchored, has_slash):
    """
    تحويل نمط gitignore إلى تعبير نمطي.

    - `*`  : أي شيء عدا `/`
    - `?`  : حرف واحد عدا `/`
    - `**` : أي شيء بما فيه `/` (و `**/` تعني صفر أو أكثر من المجلدات)
    - النمط المحتوي على `/` يُثبَّت على جذر المشروع، وإلا يطابق اسمًا في أي مستوى.
    """
    out = []
    index = 0
    length = len(pattern)

    while index < length:
        char = pattern[index]

        if char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                index += 2
                if index < length and pattern[index] == "/":
                    out.append("(?:.*/)?")
                    index += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
            index += 1
            continue

        if char == "?":
            out.append("[^/]")
            index += 1
            continue

        out.append(re.escape(char))
        index += 1

    body = "".join(out)
    if anchored or has_slash:
        return "^" + body + "$"
    return "(?:^|/)" + body + "$"


class GitIgnoreMatcher:
    """
    مطابق متدرّج لأنماط .gitignore: آخر قاعدة مطابقة هي التي تحسم،
    و`!` تلغي التجاهل. مطابقة الأنماط تتم على مسار نسبي بصيغة `/`.

    حدّ التصميم: مجلد مُتجاهَل يُقصى كليًا، لذا لا تعمل استثناءات `!`
    داخله (وهو نفس سلوك git عمليًا).
    """

    def __init__(self):
        self._rules = []  # قائمة (regex, negate, dir_only)

    @property
    def rule_count(self):
        return len(self._rules)

    def load(self, gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8") as file_handle:
                lines = file_handle.read().splitlines()
        except OSError:
            return
        self.add_lines(lines)

    def add_lines(self, lines):
        for raw_line in lines:
            line = raw_line.rstrip()
            if not line or line.startswith("#"):
                continue

            negate = line.startswith("!")
            if negate:
                line = line[1:]
            if not line:
                continue

            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]

            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            if not line:
                continue

            try:
                regex = re.compile(
                    _gitignore_glob_to_regex(line, anchored, "/" in line)
                )
            except re.error:
                continue

            self._rules.append((regex, negate, dir_only))

    def is_ignored(self, relative_path, is_dir):
        """هل المسار النسبي مُتجاهَل وفق آخر قاعدة مطابقة؟"""
        normalized = relative_path.replace(os.sep, "/")
        ignored = False
        for regex, negate, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if regex.search(normalized):
                ignored = not negate
        return ignored


def _is_always_ignored_dir(name):
    return name in ALWAYS_IGNORE_DIR_NAMES


def _is_always_ignored_file(name):
    lower = name.lower()
    if name in ALWAYS_IGNORE_FILE_NAMES:
        return True
    return lower.endswith(ALWAYS_IGNORE_FILE_SUFFIXES)


def _is_sensitive_file(name):
    lower = name.lower()
    if lower in SENSITIVE_ALLOWLIST:
        return False
    if name in SENSITIVE_FILE_NAMES:
        return True
    if lower.startswith(SENSITIVE_FILE_PREFIXES):
        return True
    return lower.endswith(SENSITIVE_FILE_SUFFIXES)


def estimate_tokens(char_count):
    """تقدير تقريبي لعدد التوكنات من عدد الأحرف."""
    return int(round(char_count / CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# دوال مساعدة
# ---------------------------------------------------------------------------

def build_safe_path(relative_path):
    """
    تحويل مسار نسبي قادم من الطلب إلى مسار مطلق آمن داخل مجلد العمل الحالي.

    يمنع الخروج عن مجلد العمل (Path Traversal) عبر التأكد من أن المسار الناتج
    يقع فعلياً داخل os.getcwd().
    """
    base_dir = os.getcwd()
    # دمج المسار النسبي مع مجلد العمل ثم تطبيعه (normalize).
    absolute_path = os.path.abspath(os.path.join(base_dir, relative_path))
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(absolute_path)

    # التأكد من أن المسار الهدف يبدأ بمجلد العمل (أو يساويه).
    if real_target != real_base and not real_target.startswith(real_base + os.sep):
        raise ValueError("المسار المطلوب خارج مجلد العمل المسموح به.")

    return absolute_path


def scan_context():
    """
    مسح مجلد العمل وإرجاع الملفات النصية مع محتوياتها وإحصاءات الحجم.

    سياسة الاستبعاد بالترتيب:
      1) مجلدات/ملفات محظورة دائمًا (توليد، اعتماديات، خرائط، أرشيفات).
      2) ملفات حساسة (أسرار/مفاتيح) — حجب صلب لا يمكن تجاوزه.
      3) قواعد .gitignore في جذر المشروع.
      4) ملفات تتجاوز سقف الحجم المفرد.
      5) ملفات ثنائية (تُكتشف بوجود بايت NUL، لا بالاعتماد على فشل فك الترميز).
      6) ما يتجاوز السقف الكلي — يُسجَّل كمتجاوز ولا يُرسل.

    عند بلوغ السقف الكلي تُرتَّب الملفات حسب العمق أولاً (الجذري قبل العميق)،
    ثم أبجديًا. هذا يضمن أن الوثائق الجذرية والملفات المعمارية تُرسل دائمًا،
    بدل اقتطاع عشوائي يترك جزءًا من المشروع خارج السياق.

    يُرجع dict فيه `files` و`stats` (عدد/أحرف/توكن/مستبعدات).
    """
    base_dir = os.getcwd()
    matcher = GitIgnoreMatcher()
    matcher.load(os.path.join(base_dir, ".gitignore"))

    skipped = []
    dropped_count = 0
    truncated = False

    def record_skip(relative_path, reason, size=None):
        nonlocal dropped_count
        dropped_count += 1
        if len(skipped) < MAX_SKIP_REPORT:
            entry = {"path": relative_path, "reason": reason}
            if size is not None:
                entry["size"] = size
            skipped.append(entry)

    # --- المرور الأول: جمع المرشحين فقط (بلا قراءة محتوى) ---
    candidates = []

    for root, dirs, filenames in os.walk(base_dir):
        relative_root = os.path.relpath(root, base_dir)
        if relative_root == ".":
            relative_root = ""

        # قصّ المجلدات المتجاهلة في مكانها لمنع النزول داخلها إطلاقًا.
        kept_dirs = []
        for dir_name in sorted(dirs):
            if _is_always_ignored_dir(dir_name):
                continue
            relative_dir = (
                os.path.join(relative_root, dir_name)
                if relative_root
                else dir_name
            )
            if matcher.is_ignored(relative_dir, True):
                continue
            kept_dirs.append(dir_name)
        dirs[:] = kept_dirs

        for filename in sorted(filenames):
            relative_path = (
                os.path.join(relative_root, filename)
                if relative_root
                else filename
            )

            if _is_always_ignored_file(filename):
                record_skip(relative_path, "generated_or_lockfile")
                continue

            if _is_sensitive_file(filename):
                record_skip(relative_path, "sensitive")
                continue

            if matcher.is_ignored(relative_path, False):
                continue

            absolute_path = os.path.join(root, filename)

            try:
                file_size = os.path.getsize(absolute_path)
            except OSError:
                record_skip(relative_path, "unreadable")
                continue

            if file_size == 0:
                record_skip(relative_path, "empty")
                continue

            if file_size > MAX_FILE_BYTES:
                record_skip(relative_path, "too_large", file_size)
                continue

            depth = relative_path.replace(os.sep, "/").count("/")
            candidates.append((depth, relative_path, absolute_path, file_size))

    # الأقرب للجذر أولًا، ثم أبجديًا (نتيجة حتمية وقابلة للتفسير).
    candidates.sort(key=lambda item: (item[0], item[1]))

    # --- المرور الثاني: القراءة ضمن الميزانية ---
    files = []
    total_chars = 0
    total_bytes = 0

    for _depth, relative_path, absolute_path, file_size in candidates:
        if total_bytes + file_size > MAX_TOTAL_BYTES:
            truncated = True
            record_skip(relative_path, "total_budget", file_size)
            continue

        try:
            with open(absolute_path, "rb") as file_handle:
                head = file_handle.read(8192)
                # وجود بايت NUL مؤشر قوي على ملف ثنائي.
                if b"\x00" in head:
                    record_skip(relative_path, "binary", file_size)
                    continue
                raw = head + file_handle.read()
        except OSError:
            record_skip(relative_path, "unreadable")
            continue

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            record_skip(relative_path, "non_utf8", file_size)
            continue

        files.append({"path": relative_path, "content": content})
        total_chars += len(content)
        total_bytes += len(raw)

    stats = {
        "file_count": len(files),
        "total_chars": total_chars,
        "total_bytes": total_bytes,
        "approx_tokens": estimate_tokens(total_chars),
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "truncated": truncated,
        "dropped_count": dropped_count,
        "skipped": skipped,
        "gitignore_rules": matcher.rule_count,
    }
    return {"files": files, "stats": stats}


# ---------------------------------------------------------------------------
# مطابقة نص `find` بمرونة (Replace Matching Helpers)
#
# السبب: نص `find` يأتي من `innerText` في المتصفح، وقد يختلف عن محتوى الملف
# على القرص في نهايات الأسطر أو سطر جديد زائد في النهاية أو المسافات البادئة.
# لذلك نجرّب عدة استراتيجيات متدرجة بدل المطابقة الحرفية الصارمة فقط.
# ---------------------------------------------------------------------------

def _dominant_newline(text):
    """إرجاع نمط نهاية السطر السائد في النص: '\r\n' أو '\n'."""
    crlf_count = text.count("\r\n")
    lf_only = text.count("\n") - crlf_count
    return "\r\n" if crlf_count > lf_only else "\n"


def align_newlines(text, reference):
    """توحيد نهايات الأسطر في `text` لتطابق النمط السائد في `reference`."""
    newline = _dominant_newline(reference)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline == "\r\n":
        return normalized.replace("\n", "\r\n")
    return normalized


# الحد الأدنى لنسبة التشابه المسموح بها في المطابقة الضبابية الاحتياطية.
FUZZY_THRESHOLD = 0.8
# أقل عدد أسطر يسمح بتطبيق المطابقة الضبابية (لتفادي التطابق الخاطئ).
FUZZY_MIN_LINES = 3


def _normalized_lines(text):
    """تحويل النص إلى قائمة أسطر مع توحيد نهايات الأسطر (بدون نهايات)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


def find_fuzzy_span(content, find_text, threshold=FUZZY_THRESHOLD):
    """
    مطابقة ضبابية احتياطية: تبحث عن أقرب نافذة أسطر في `content` تشبه
    `find_text` بنسبة تتجاوز `threshold` (بعد تجاهل المسافات البادئة/الزائدة).

    تُستخدم عندما تفشل كل المطابقات الدقيقة (حرفية/نهايات أسطر/مسافات)، أي
    عندما يختلف نص الذكاء الاصطناعي عن الملف بفارق بسيط. تُرجع
    (start, end, ratio) أو None. لا تُطبَّق على المقاطع القصيرة جداً تفادياً
    لاستبدال موضع خاطئ.
    """
    content_lines = content.splitlines(keepends=True)
    content_stripped = [line.strip() for line in content_lines]

    find_lines = _normalized_lines(find_text)
    while find_lines and find_lines[-1] == "":
        find_lines.pop()
    while find_lines and find_lines[0] == "":
        find_lines.pop(0)
    find_stripped = [line.strip() for line in find_lines]

    window = len(find_stripped)
    if window < FUZZY_MIN_LINES or window > len(content_stripped):
        return None

    anchor = find_stripped[0]
    best_index = -1
    best_ratio = 0.0

    for index in range(len(content_stripped) - window + 1):
        # مرشّح سريع: يجب أن يشبه السطر الأول مرساة البحث.
        if difflib.SequenceMatcher(
            None, anchor, content_stripped[index], autojunk=False
        ).ratio() < 0.6:
            continue
        ratio = difflib.SequenceMatcher(
            None,
            find_stripped,
            content_stripped[index:index + window],
            autojunk=False,
        ).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_index = index

    if best_index == -1 or best_ratio < threshold:
        return None

    start = sum(len(line) for line in content_lines[:best_index])
    end = start + sum(
        len(line) for line in content_lines[best_index:best_index + window]
    )
    return start, end, best_ratio


def find_whitespace_tolerant_span(content, find_text):
    """
    مطابقة على مستوى الأسطر مع تجاهل المسافات البادئة/الزائدة ونهايات
    الأسطر. تُرجع (start, end) بمواضع المنطقة الأصلية في `content` أو None.
    """
    find_lines = [line.strip() for line in find_text.split("\n")]
    # إزالة الأسطر الفارغة الزائدة من بداية/نهاية المقطع.
    while find_lines and find_lines[-1] == "":
        find_lines.pop()
    while find_lines and find_lines[0] == "":
        find_lines.pop(0)
    if not find_lines:
        return None

    content_lines = content.splitlines(keepends=True)
    stripped = [line.strip() for line in content_lines]
    window = len(find_lines)

    for index in range(len(stripped) - window + 1):
        if stripped[index:index + window] == find_lines:
            start = sum(len(line) for line in content_lines[:index])
            end = start + sum(
                len(line) for line in content_lines[index:index + window]
            )
            return start, end
    return None


def resolve_replace_span(content, find_text):
    """
    البحث عن منطقة `find` داخل `content` بترتيب متدرج:
      1) مطابقة حرفية مباشرة.
      2) بعد مواءمة نهايات الأسطر مع الملف.
      3) بعد إزالة سطر جديد زائد من النهاية.
      4) مطابقة متسامحة مع المسافات البادئة/الزائدة.
    تُرجع (start, end, strategy) أو (None, None, None) عند الفشل الكامل.
    """
    index = content.find(find_text)
    if index != -1:
        return index, index + len(find_text), "exact"

    variant = align_newlines(find_text, content)
    if variant != find_text:
        index = content.find(variant)
        if index != -1:
            return index, index + len(variant), "eol-normalized"

    trimmed = find_text.rstrip("\r\n")
    if trimmed and trimmed != find_text:
        index = content.find(trimmed)
        if index != -1:
            return index, index + len(trimmed), "trimmed-trailing"
        variant = align_newlines(trimmed, content)
        if variant != trimmed:
            index = content.find(variant)
            if index != -1:
                return index, index + len(variant), "trimmed-trailing+eol"

    span = find_whitespace_tolerant_span(content, find_text)
    if span:
        return span[0], span[1], "whitespace-tolerant"

    fuzzy = find_fuzzy_span(content, find_text)
    if fuzzy:
        return fuzzy[0], fuzzy[1], f"fuzzy({fuzzy[2]:.2f})"

    return None, None, None


def _format_diagnostic_message(diagnostic):
    """صياغة رسالة خطأ موجزة تُعرض للمستخدم في الواجهة."""
    parts = [f"فشل مطابقة find في «{diagnostic['file']}»."]
    parts.append(diagnostic["reason"])
    if "matched_lines" in diagnostic:
        parts.append(
            f"تطابق: {diagnostic['matched_lines']}/"
            f"{diagnostic['find_total_lines']} سطر."
        )
    if "expected_line" in diagnostic:
        parts.append(f"المتوقع: {diagnostic['expected_line']!r}")
        parts.append(f"الموجود: {diagnostic['actual_line']!r}")
    if "closest_ratio" in diagnostic:
        parts.append(
            f"أقرب تطابق: {diagnostic['closest_ratio'] * 100:.0f}% "
            f"(الحد المطلوب {FUZZY_THRESHOLD * 100:.0f}%)."
        )
    return " ".join(parts)


def build_match_diagnostic(content, find_text, relative_path):
    """
    تشخيص سبب فشل المطابقة: أول سطر مطابق، عدد الأسطر المتطابقة، والسطر
    الذي بدأ عنده الاختلاف، مع رسالة جاهزة للعرض.
    """
    find_lines = [
        line.strip() for line in find_text.split("\n") if line.strip()
    ]
    content_lines = content.splitlines()
    diagnostic = {
        "file": relative_path,
        "reason": "لم يتم العثور على أي جزء مطابق من نص find في الملف.",
        "find_total_lines": len(find_lines),
        "file_total_lines": len(content_lines),
    }

    if not find_lines:
        diagnostic["reason"] = "نص find فارغ."
        diagnostic["message"] = _format_diagnostic_message(diagnostic)
        return diagnostic

    first_line = find_lines[0]
    for position, line in enumerate(content_lines):
        if line.strip() != first_line:
            continue

        matched = 0
        while (
            matched < len(find_lines)
            and position + matched < len(content_lines)
            and content_lines[position + matched].strip() == find_lines[matched]
        ):
            matched += 1

        diagnostic["first_line_at"] = position + 1
        diagnostic["matched_lines"] = matched

        if matched == len(find_lines):
            diagnostic["reason"] = (
                "تم العثور على المطابقة المتسامحة لكن تعذّر تحديد الموضع."
            )
            break

        diff_position = position + matched
        diagnostic["reason"] = (
            f"تطابقت {matched} أسطر بدءاً من السطر {position + 1}، "
            f"ثم اختلف السطر {diff_position + 1}."
        )
        diagnostic["expected_line"] = find_lines[matched]
        diagnostic["actual_line"] = (
            content_lines[diff_position]
            if diff_position < len(content_lines)
            else "<نهاية الملف>"
        )
        break
    else:
        diagnostic["reason"] = (
            "أول سطر من نص find غير موجود في الملف حتى بعد تجاهل المسافات."
        )

    # أقرب نسبة تشابه (حتى لو لم تبلغ حد المطابقة الضبابية) لمساعدة المستخدم.
    fuzzy = find_fuzzy_span(content, find_text, threshold=0.0)
    if fuzzy:
        diagnostic["closest_ratio"] = round(fuzzy[2], 3)

    diagnostic["message"] = _format_diagnostic_message(diagnostic)
    return diagnostic


# ---------------------------------------------------------------------------
# نقاط النهاية (Endpoints)
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """نقطة فحص بسيطة لتأكيد أن الخادم يعمل، مع حدود المسح الحالية."""
    return jsonify(
        {
            "status": "running",
            "cwd": os.getcwd(),
            "limits": {
                "max_file_bytes": MAX_FILE_BYTES,
                "max_total_bytes": MAX_TOTAL_BYTES,
                "max_total_tokens_estimate": estimate_tokens(MAX_TOTAL_BYTES),
            },
        }
    )


@app.route("/context", methods=["GET"])
def context():
    """
    إرجاع سياق المشروع: الملفات النصية المسموح بها مع مساراتها ومحتوياتها،
    إضافة إلى `stats` (عدد الملفات، الحجم، تقدير التوكن، والملفات المستبعدة).
    """
    try:
        result = scan_context()
        stats = result["stats"]
        print(
            f"[CONTEXT] {stats['file_count']} ملف | "
            f"~{stats['approx_tokens']} توكن | "
            f"مستبعد: {stats['dropped_count']}"
            f"{' | تم الاقتطاع' if stats['truncated'] else ''}"
        )
        return jsonify({"files": result["files"], "stats": stats})
    except Exception as exc:  # noqa: BLE001 - نريد إرجاع أي خطأ بصيغة JSON.
        return jsonify({"error": str(exc)}), 500


@app.route("/write", methods=["POST"])
def write_file():
    """
    كتابة/تعديل ملف بنمطين صريحين عبر حقل `mode`:

    1) mode = "full"  -> كتابة/استبدال الملف بالكامل.
       Body: {"mode": "full", "path": "...", "content": "..."}

    2) mode = "replace" -> بحث عن `find` واستبداله بـ `replace` مرة واحدة.
       Body: {"mode": "replace", "path": "...", "find": "...", "replace": "..."}
       يجرّب المطابقة الحرفية أولاً، ثم يلجأ تلقائياً إلى مطابقة متسامحة
       (تجاهل نهايات الأسطر/سطر جديد زائد/المسافات البادئة والزائدة).
       عند الفشل الكامل يُرجع 404 مع تشخيص يوضّح سبب عدم التطابق.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "الطلب يجب أن يحتوي على JSON صالح."}), 400

        relative_path = data.get("path")
        if not relative_path:
            return jsonify({"error": "الحقل المطلوب: path."}), 400

        # النمط الافتراضي هو الكتابة الكاملة عند غياب mode (توافقية للخلف).
        mode = (data.get("mode") or "full").lower()

        # التحقق من المسار وبناء المسار المطلق الآمن (حماية من Path Traversal).
        try:
            absolute_path = build_safe_path(relative_path)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # إنشاء المجلدات الأبوية عند الحاجة.
        parent_dir = os.path.dirname(absolute_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # ------------------------- نمط الكتابة الكاملة -------------------------
        if mode == "full":
            content = data.get("content")
            if content is None:
                return (
                    jsonify({"error": "الحقل المطلوب في نمط full: content."}),
                    400,
                )

            # newline="" يمنع الترجمة التلقائية لنهايات الأسطر.
            with open(
                absolute_path, "w", encoding="utf-8", newline=""
            ) as file_handle:
                file_handle.write(content)

            print(f"[WRITE] Mode: FULL | File: {relative_path}")
            return jsonify(
                {
                    "status": "success",
                    "mode": "full",
                    "file": relative_path,
                }
            )

        # ------------------- نمط البحث والاستبدال المباشر -------------------
        if mode == "replace":
            find_text = data.get("find")
            replace_text = data.get("replace")

            if find_text is None or replace_text is None:
                return (
                    jsonify(
                        {
                            "error": (
                                "الحقول المطلوبة في نمط replace: find و replace."
                            )
                        }
                    ),
                    400,
                )

            if not os.path.exists(absolute_path):
                return (
                    jsonify(
                        {
                            "error": (
                                f"تعذّر تطبيق التعديل: الملف غير موجود "
                                f"({relative_path})."
                            )
                        }
                    ),
                    404,
                )

            # newline="" نقرأ البايتات كما هي للحفاظ على نمط نهايات أسطر الملف.
            with open(
                absolute_path, "r", encoding="utf-8", newline=""
            ) as file_handle:
                current_content = file_handle.read()

            # البحث عن موضع `find` بعدة استراتيجيات متدرجة (متسامحة).
            start, end, strategy = resolve_replace_span(
                current_content, find_text
            )

            if start is None:
                diagnostic = build_match_diagnostic(
                    current_content, find_text, relative_path
                )
                print(
                    f"[WRITE] Mode: REPLACE | File: {relative_path} | "
                    f"MATCH FAILED | {diagnostic['reason']}"
                )
                return (
                    jsonify(
                        {
                            "error": diagnostic["message"],
                            "file": relative_path,
                            "find": find_text,
                            "diagnostic": diagnostic,
                        }
                    ),
                    404,
                )

            # مواءمة نهايات أسطر الكود البديل مع نمط الملف المحفوظ.
            adapted_replace = align_newlines(replace_text, current_content)
            updated_content = (
                current_content[:start] + adapted_replace + current_content[end:]
            )

            # newline="" للحفاظ على نهايات أسطر الملف الأصلية دون ترجمة.
            with open(
                absolute_path, "w", encoding="utf-8", newline=""
            ) as file_handle:
                file_handle.write(updated_content)

            print(
                f"[WRITE] Mode: REPLACE | File: {relative_path} | "
                f"MATCH OK ({strategy})"
            )
            return jsonify(
                {
                    "status": "success",
                    "mode": "replace",
                    "file": relative_path,
                    "strategy": strategy,
                }
            )

        # mode غير معروف.
        return (
            jsonify(
                {
                    "error": (
                        f"قيمة mode غير مدعومة: '{mode}'. "
                        "القيم المدعومة: full, replace."
                    )
                }
            ),
            400,
        )

    except Exception as exc:  # noqa: BLE001 - إرجاع أي استثناء بصيغة JSON.
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# نقطة الدخول
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Vibe Coding Local Bridge يعمل الآن على:")
    print("  http://127.0.0.1:5000")
    print(f"  Working Directory: {os.getcwd()}")
    print("=" * 60)
    # host=127.0.0.1 لضمان الوصول المحلي فقط.
    app.run(host="127.0.0.1", port=5000, debug=False)

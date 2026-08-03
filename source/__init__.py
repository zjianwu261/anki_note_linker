# -*- coding: utf-8 -*-
"""Note Linker — 给 Anki 卡片建立关联

功能:
1. 手动关联: 在浏览器中选中多条笔记, 右键 -> 关联所选笔记
2. 自动关联: 工具菜单 -> 基于 TF-IDF 语义相似度自动建立关联
3. 知识图谱: 工具菜单 -> 交互式力导向图查看所有关联
4. 复习时"相关卡片"字段中的链接可点击, 跳转到对应笔记
"""

import html as html_mod
import re

__version__ = "0.7"

from aqt import dialogs, gui_hooks, mw
from aqt.qt import QKeySequence, QMenu, qconnect
from aqt.utils import askUser, getText, showInfo, tooltip

from . import llm, similarity

LINK_RE = re.compile(r"notelinker:open:(\d+)")
CSS_MARK = "/* note-linker-css */"
NL_CSS = """
%s
.nl-links-box { margin-top: 14px; padding-top: 8px; border-top: 1px dashed #999;
  font-size: 14px; text-align: left; }
.nl-links-title { color: #888; font-size: 12px; margin-bottom: 4px; }
a.nl-link { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 8px;
  border-radius: 10px; background: rgba(66,133,244,.12); color: #4285f4;
  text-decoration: none; }
a.nl-link:hover { background: rgba(66,133,244,.25); }
""" % CSS_MARK


# ---------------- 配置 ----------------

def get_config():
    cfg = mw.addonManager.getConfig(__name__) or {}
    cfg.setdefault("field_name", "相关卡片")
    cfg.setdefault("similarity_threshold", 0.28)
    cfg.setdefault("max_links_per_note", 5)
    cfg.setdefault("search_query", "")
    cfg.setdefault("add_to_template", True)
    cfg.setdefault("exclude_fields", [])
    cfg.setdefault("api_base", "https://api.deepseek.com")
    cfg.setdefault("api_key", "")
    cfg.setdefault("llm_model", "deepseek-v4-flash")
    cfg.setdefault("llm_batch_size", 40)
    cfg.setdefault("llm_snippet_chars", 120)
    cfg.setdefault("link_title_chars", 120)
    cfg.setdefault("topic_tag_prefix", "NL::")
    cfg.setdefault("llm_selected_decks", [])
    return cfg


def save_config_keys(**kv):
    """只写入指定的键, 不覆盖用户手改的其他配置。"""
    raw = mw.addonManager.getConfig(__name__) or {}
    raw.update(kv)
    mw.addonManager.writeConfig(__name__, raw)


def field_name():
    return get_config()["field_name"]


# ---------------- 字段/模板管理 ----------------

def _model_field_names(model):
    return [f["name"] for f in model["flds"]]


def ensure_field_on_models(mids):
    """确保这些笔记类型都有链接字段。返回是否有模型被修改。"""
    fname = field_name()
    mm = mw.col.models
    changed = False
    for mid in set(mids):
        model = mm.get(mid)
        if model is None or fname in _model_field_names(model):
            continue
        fld = mm.new_field(fname)
        mm.add_field(model, fld)
        if get_config().get("add_to_template"):
            _add_field_to_templates(model, fname)
        if CSS_MARK not in model.get("css", ""):
            model["css"] = model.get("css", "") + "\n" + NL_CSS
        mm.update_dict(model)
        changed = True
    return changed


def _add_field_to_templates(model, fname):
    snippet = (
        "\n\n{{#%s}}<div class=\"nl-links-box\">"
        "<div class=\"nl-links-title\">🔗 相关卡片</div>{{%s}}</div>{{/%s}}"
        % (fname, fname, fname)
    )
    for tmpl in model["tmpls"]:
        if ("{{%s}}" % fname) not in tmpl["afmt"]:
            tmpl["afmt"] += snippet


# ---------------- 链接读写 ----------------

def _note_text(note):
    try:
        text = similarity.strip_html(note.fields[0]).strip()
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = "(无标题 %d)" % note.id
    return text


def note_title(note, maxlen=None):
    if maxlen is None:
        maxlen = int(get_config().get("link_title_chars", 120))
    text = _note_text(note)
    return text[:maxlen] + ("…" if len(text) > maxlen else "")


def get_link_ids(note):
    fname = field_name()
    if fname not in note:
        return []
    seen, out = set(), []
    for m in LINK_RE.findall(note[fname]):
        nid = int(m)
        if nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out




def build_links_html(nids):
    """生成轻量链接 HTML。

    刻意保持精简 (每条链接仅 ~200 字节), 避免撑大集合体积拖慢同步。
    桌面端: pycmd 由插件接管, 直接打开卡片;
    AnkiMobile (iOS): 走 href 的 anki:// URL scheme 打开搜索。
    """
    maxlen = int(get_config().get("link_title_chars", 120))
    parts = []
    for nid in nids:
        try:
            other = mw.col.get_note(nid)
        except Exception:
            continue  # 笔记已删除
        text = _note_text(other)[:500]  # 硬上限, 防止字段过大
        if len(text) <= maxlen:
            inner, more = html_mod.escape(text), ""
        else:
            inner = (
                html_mod.escape(text[:maxlen])
                + '<span style="display:none">%s</span>' % html_mod.escape(text[maxlen:])
            )
            more = (
                '<span class="nl-more" style="cursor:pointer;color:#4285f4;'
                'font-size:0.85em;padding:0 4px;" '
                "onclick=\"var s=this.previousElementSibling.querySelector('span');"
                "var h=s.style.display=='none';s.style.display=h?'inline':'none';"
                "this.textContent=h?'收起':'…展开';\">…展开</span>"
            )
        parts.append(
            '<li style="margin:3px 0;">'
            '<a class="nl-link" href="anki://x-callback-url/search?query=nid%%3A%d" '
            "onclick=\"if(/QtWebEngine/i.test(navigator.userAgent))"
            "{pycmd('notelinker:open:%d');return false;}return true;\">%s</a>%s"
            "</li>" % (nid, nid, inner, more)
        )
    if not parts:
        return ""
    return (
        '<ul class="nl-links" style="margin:6px 0 0;padding-left:20px;'
        'list-style:disc;text-align:left;">%s</ul>' % "".join(parts)
    )


def set_links(note, nids):
    fname = field_name()
    if fname not in note:
        return False
    note[fname] = build_links_html([n for n in nids if n != note.id])
    return True


# ---------------- 手动关联 (浏览器右键) ----------------

def _selected_notes(browser):
    try:
        return list(browser.selected_notes())
    except AttributeError:
        return list(browser.selectedNotes())


def link_selected(browser):
    nids = _selected_notes(browser)
    if len(nids) < 2:
        tooltip("请至少选择两条笔记")
        return
    if len(nids) > 50 and not askUser("选中了 %d 条笔记, 将两两互相关联, 继续?" % len(nids)):
        return
    mids = [mw.col.get_note(nid).mid for nid in nids]
    ensure_field_on_models(mids)
    for nid in nids:
        note = mw.col.get_note(nid)
        merged = get_link_ids(note)
        for other in nids:
            if other != nid and other not in merged:
                merged.append(other)
        if set_links(note, merged):
            mw.col.update_note(note)
    mw.reset()
    tooltip("已互相关联 %d 条笔记" % len(nids))


def unlink_selected(browser):
    nids = _selected_notes(browser)
    if not nids:
        tooltip("请先选择笔记")
        return
    fname = field_name()
    count = 0
    for nid in nids:
        note = mw.col.get_note(nid)
        if fname in note and note[fname]:
            note[fname] = ""
            mw.col.update_note(note)
            count += 1
    mw.reset()
    tooltip("已清除 %d 条笔记的关联" % count)


def on_browser_context_menu(browser, menu):
    menu.addSeparator()
    sub = menu.addMenu("Note Linker")
    a1 = sub.addAction("🔗 关联所选笔记")
    qconnect(a1.triggered, lambda: link_selected(browser))
    a2 = sub.addAction("✂️ 清除所选笔记的关联")
    qconnect(a2.triggered, lambda: unlink_selected(browser))

# ---------------- LLM 主题关联 (DeepSeek) ----------------

def _ensure_api_key():
    cfg = get_config()
    if cfg.get("api_key"):
        return True
    key, ok = getText(
        "请输入 DeepSeek API Key (在 platform.deepseek.com 获取):",
        title="Note Linker",
    )
    if not ok or not key.strip():
        return False
    save_config_keys(api_key=key.strip())
    return True


def _escape_deck(name):
    """转义牌组名, 使其能安全放进 deck:"..." 搜索。"""
    out = name.replace("\\", "\\\\").replace('"', '\\"')
    return out.replace("*", "\\*").replace("_", "\\_")


def _build_deck_tree(tree_widget):
    """按 :: 层级把所有牌组建成可勾选的树, 返回 {全名: QTreeWidgetItem}。"""
    from aqt.qt import Qt, QTreeWidgetItem

    names = sorted(
        (d.name for d in mw.col.decks.all_names_and_ids()),
        key=lambda s: s.lower(),
    )
    items = {}
    flags = (
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsUserCheckable
        | Qt.ItemFlag.ItemIsAutoTristate
    )
    for full in names:
        parts = full.split("::")
        for i in range(len(parts)):
            path = "::".join(parts[: i + 1])
            if path in items:
                continue
            parent = items.get("::".join(parts[:i])) if i else None
            item = QTreeWidgetItem(parent or tree_widget, [parts[i]])
            item.setFlags(flags)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            items[path] = item
    return items


def _checked_decks(tree_widget):
    """收集最上层被完全勾选的牌组 (deck:X 已包含子牌组)。"""
    from aqt.qt import Qt

    picked = []

    def walk(item):
        state = item.checkState(0)
        if state == Qt.CheckState.Checked:
            picked.append(item.data(0, Qt.ItemDataRole.UserRole))
            return
        if state == Qt.CheckState.PartiallyChecked:
            for i in range(item.childCount()):
                walk(item.child(i))

    for i in range(tree_widget.topLevelItemCount()):
        walk(tree_widget.topLevelItem(i))
    return picked


def pick_decks(preselected=None):
    """弹出牌组勾选对话框。返回选中的牌组全名列表, 取消返回 None。"""
    from aqt.qt import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        Qt,
        QTreeWidget,
        QVBoxLayout,
    )

    dlg = QDialog(mw)
    dlg.setWindowTitle("LLM 主题关联 — 选择牌组")
    dlg.resize(460, 540)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("勾选要参与主题分类的牌组 (勾选父牌组会包含其子牌组):"))

    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    items = _build_deck_tree(tree)
    tree.expandAll()
    layout.addWidget(tree)

    for name in preselected or []:
        item = items.get(name)
        if item is not None:
            item.setCheckState(0, Qt.CheckState.Checked)

    count_label = QLabel("")
    row = QHBoxLayout()
    btn_all = QPushButton("全选")
    btn_none = QPushButton("全不选")
    row.addWidget(btn_all)
    row.addWidget(btn_none)
    row.addStretch()
    row.addWidget(count_label)
    layout.addLayout(row)

    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
    layout.addWidget(box)

    def set_all(state):
        for i in range(tree.topLevelItemCount()):
            tree.topLevelItem(i).setCheckState(0, state)

    def refresh():
        n = len(_checked_decks(tree))
        count_label.setText("已选 %d 个牌组" % n)
        ok_btn.setEnabled(n > 0)

    qconnect(btn_all.clicked, lambda: set_all(Qt.CheckState.Checked))
    qconnect(btn_none.clicked, lambda: set_all(Qt.CheckState.Unchecked))
    qconnect(tree.itemChanged, lambda *_: refresh())
    qconnect(box.accepted, dlg.accept)
    qconnect(box.rejected, dlg.reject)
    refresh()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return _checked_decks(tree)


def _decks_to_query(decks):
    return " OR ".join('deck:"%s"' % _escape_deck(d) for d in decks)


def _collect_docs(query):
    """返回 {nid: 纯文本} (排除链接字段和 exclude_fields)。"""
    cfg = get_config()
    exclude = set(cfg.get("exclude_fields", [])) | {field_name()}
    docs = {}
    for nid in mw.col.find_notes(query):
        note = mw.col.get_note(nid)
        texts = [
            similarity.strip_html(val) for name, val in note.items()
            if name not in exclude and val.strip()
        ]
        text = " ".join(" ".join(texts).split())
        if text:
            docs[nid] = text
    return docs


def llm_topic_link():
    if not _ensure_api_key():
        return
    cfg = get_config()
    decks = pick_decks(cfg.get("llm_selected_decks") or [])
    if not decks:
        return
    save_config_keys(llm_selected_decks=decks)
    query = _decks_to_query(decks)
    docs = _collect_docs(query)
    if len(docs) < 2:
        showInfo("所选牌组内笔记不足两条 (找到 %d 条)。" % len(docs))
        return
    est_batches = len(docs) // int(cfg["llm_batch_size"]) + 1
    deck_list = "、".join(decks[:5]) + ("…" if len(decks) > 5 else "")
    if not askUser(
        "牌组: %s\n\n"
        "将把 %d 条笔记的摘要分 %d 批发送给 %s 进行主题分类,\n"
        "并写入主题标签 (%s主题名) 和\"相关卡片\"链接。\n\n"
        "费用很低 (约每千卡几分钱), 内容摘要会发送到 API。继续?"
        % (deck_list, len(docs), est_batches, cfg["llm_model"], cfg["topic_tag_prefix"])
    ):
        return

    items = list(docs.items())
    mw.progress.start(label="LLM 主题分类中…", immediate=True)

    def progress_cb(done, total):
        mw.taskman.run_on_main(
            lambda: mw.progress.update(
                label="LLM 主题分类中… (%d/%d 批)" % (done + 1, total)
            )
        )

    def compute():
        topic_map, skipped = llm.assign_topics(cfg, items, progress_cb)
        topics = sorted(set(topic_map.values()))
        merge = llm.merge_topics(cfg, topics)
        if merge:
            topic_map = {nid: merge.get(t, t) for nid, t in topic_map.items()}
        return topic_map, skipped

    def on_done(fut):
        try:
            topic_map, skipped = fut.result()
        except Exception as e:
            mw.progress.finish()
            showInfo("LLM 调用失败: %s" % e)
            return
        try:
            _apply_topics(topic_map, docs, skipped)
        finally:
            mw.progress.finish()

    mw.taskman.run_in_background(compute, on_done)


def _apply_topics(topic_map, docs, skipped=None):
    if not topic_map:
        showInfo("LLM 未返回有效的主题分类结果。")
        return
    cfg = get_config()
    prefix = cfg["topic_tag_prefix"]
    max_links = int(cfg.get("max_links_per_note", 5))

    groups = {}
    for nid, topic in topic_map.items():
        groups.setdefault(topic, []).append(nid)

    mids = []
    for nid in topic_map:
        try:
            mids.append(mw.col.get_note(nid).mid)
        except Exception:
            pass
    ensure_field_on_models(mids)

    # 每个主题组内: 用 TF-IDF 排序挑最相似的; 不够就按组内顺序补齐
    planned_links = {}
    for topic, nids in groups.items():
        if len(nids) < 2:
            continue
        vecs = similarity.build_vectors({n: docs.get(n, "") for n in nids})
        for nid in nids:
            others = [n for n in nids if n != nid]
            ranked = sorted(
                others,
                key=lambda o: -similarity.cosine_of(vecs.get(nid, {}), vecs.get(o, {})),
            )
            planned_links[nid] = ranked[:max_links]

    updated = 0
    for nid, topic in topic_map.items():
        try:
            note = mw.col.get_note(nid)
        except Exception:
            continue
        # 更新主题标签 (移除旧的前缀标签)
        note.tags = [t for t in note.tags if not t.startswith(prefix)]
        note.tags.append(prefix + topic)
        # 更新链接 (保留已有的手动/自动链接, 新链接排在后面)
        new_links = planned_links.get(nid, [])
        if field_name() in note:
            existing = get_link_ids(note)
            merged = existing + [n for n in new_links if n not in existing]
            set_links(note, merged)
        mw.col.update_note(note)
        updated += 1

    mw.reset()
    top = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:15]
    lines = ["%s%s: %d 张" % (prefix, t, len(ns)) for t, ns in top]
    more = "" if len(groups) <= 15 else "\n… 共 %d 个主题" % len(groups)
    skip_note = ""
    if skipped:
        skip_note = (
            "\n\n⚠️ %d 张卡片被 DeepSeek 内容审核拒绝, 已跳过。"
            "\n可在浏览器搜索: %s" % (
                len(skipped),
                " OR ".join("nid:%d" % n for n in skipped[:10])
                + ("…" if len(skipped) > 10 else ""),
            )
        )
    showInfo(
        "LLM 主题关联完成: %d 条笔记, %d 个主题。\n\n%s%s%s"
        % (updated, len(groups), "\n".join(lines), more, skip_note)
    )


# ---------------- 链接点击跳转 ----------------

def open_note_in_browser(nid):
    browser = dialogs.open("Browser", mw)
    try:
        browser.search_for("nid:%d" % nid)
    except AttributeError:
        browser.form.searchEdit.lineEdit().setText("nid:%d" % nid)
        browser.onSearchActivated()


_previewer = None  # 复用单个预览窗口, 避免越开越多
_edit_dialog = None  # 复用单个编辑窗口


def _clear_previewer():
    global _previewer
    _previewer = None


_history = []  # 跳转历史 (Card 对象)
_hist_idx = -1


def _close_plugin_windows():
    """退出/切换配置前主动关闭插件窗口。

    否则 QDialog 会残留到 Python 解释器退出时才被 sip 销毁,
    那时 WebEngine 已被拆除, 销毁内嵌网页视图会导致段错误 (SIGSEGV)。
    """
    global _previewer, _edit_dialog, _hist_idx
    for w in (_edit_dialog, _previewer):
        try:
            if w is not None:
                w.close()
        except Exception:
            pass
    _previewer = None
    _edit_dialog = None
    _history.clear()
    _hist_idx = -1


def open_note_preview(nid):
    """点击链接直接打开卡片预览; ‹ › 在浏览历史间前后翻; 可编辑。

    失败时回退到 Browser。
    """
    global _previewer, _history, _hist_idx
    try:
        from aqt.browser.previewer import MultiCardPreviewer
        from aqt.qt import QDialogButtonBox

        note = mw.col.get_note(nid)
        cids = note.card_ids()
        if not cids:
            raise ValueError("note has no cards")
        card = mw.col.get_card(cids[0])

        # 入栈: 若不是当前这张, 丢弃"前进"分支后追加
        if not (0 <= _hist_idx < len(_history) and _history[_hist_idx].nid == nid):
            _history = _history[: _hist_idx + 1]
            _history.append(card)
            _hist_idx = len(_history) - 1

        if _previewer is not None:
            try:
                _previewer.render_card()
                _previewer.activateWindow()
                _previewer.raise_()
                return
            except Exception:
                _previewer = None  # 窗口已失效, 重建

        class _HistoryPreviewer(MultiCardPreviewer):
            _last_card_id = 0

            def __init__(self):
                super().__init__(parent=None, mw=mw, on_close=_clear_previewer)

            def card(self):
                if 0 <= _hist_idx < len(_history):
                    return _history[_hist_idx]
                return None

            def card_changed(self):
                c = self.card()
                if not c:
                    return True
                changed = c.id != self._last_card_id
                self._last_card_id = c.id
                return changed

            def _create_gui(self):
                super()._create_gui()
                try:
                    from aqt.qt import QKeySequence

                    btn = self.bbox.addButton(
                        "编辑", QDialogButtonBox.ButtonRole.ActionRole
                    )
                    btn.setAutoDefault(False)
                    btn.setShortcut(QKeySequence("E"))
                    btn.setToolTip("快捷键: E")
                    qconnect(btn.clicked, self._on_edit_clicked)
                except Exception:
                    pass

            def _on_edit_clicked(self):
                c = self.card()
                if c:
                    open_note_editor(c.nid)

            def _on_prev_card(self):
                global _hist_idx
                if _hist_idx > 0:
                    _hist_idx -= 1
                    self.render_card()

            def _on_next_card(self):
                global _hist_idx
                if _hist_idx + 1 < len(_history):
                    _hist_idx += 1
                    self.render_card()

            def _should_enable_prev(self):
                return super()._should_enable_prev() or _hist_idx > 0

            def _should_enable_next(self):
                return (
                    super()._should_enable_next()
                    or _hist_idx + 1 < len(_history)
                )

            def _render_scheduled(self):
                super()._render_scheduled()
                self._updateButtons()

        _previewer = _HistoryPreviewer()
        _previewer.open()
    except Exception:
        _previewer = None
        open_note_in_browser(nid)


def open_note_editor(nid):
    """独立编辑器窗口编辑笔记; 已开则换到该笔记 (失败回退到 Browser)。"""
    global _edit_dialog
    try:
        import aqt.editor
        from aqt.qt import QDialog, Qt, QVBoxLayout, QWidget

        note = mw.col.get_note(nid)

        if _edit_dialog is not None:
            try:
                _edit_dialog.set_note(note)
                _edit_dialog.activateWindow()
                _edit_dialog.raise_()
                return
            except Exception:
                _edit_dialog = None

        class _NoteEditDialog(QDialog):
            def __init__(self, note):
                super().__init__(None, Qt.WindowType.Window)
                self.setWindowTitle("编辑笔记 — Note Linker")
                self.setMinimumSize(560, 440)
                box = QVBoxLayout(self)
                box.setContentsMargins(0, 0, 0, 0)
                w = QWidget(self)
                box.addWidget(w)
                self.editor = aqt.editor.Editor(mw, w, self)
                self.set_note(note)
                self.show()

            def set_note(self, note):
                try:
                    self.editor.set_note(note)
                except AttributeError:
                    self.editor.setNote(note)

            def _after_save(self):
                global _edit_dialog
                try:
                    self.editor.cleanup()
                except Exception:
                    pass
                _edit_dialog = None
                if _previewer is not None:
                    try:
                        _previewer.render_card()  # 编辑后刷新预览
                    except Exception:
                        pass
                QDialog.reject(self)

            def reject(self):  # Esc / 关闭按钮都会走这里
                try:
                    self.editor.call_after_note_saved(self._after_save)
                except AttributeError:
                    self.editor.saveNow(self._after_save)

        _edit_dialog = _NoteEditDialog(note)
    except Exception:
        _edit_dialog = None
        open_note_in_browser(nid)


def on_js_message(handled, message, context):
    if isinstance(message, str) and message.startswith("notelinker:"):
        parts = message.split(":")
        if len(parts) == 3 and parts[1] in ("open", "edit"):
            try:
                nid = int(parts[2])
            except ValueError:
                return (True, None)
            if parts[1] == "open":
                open_note_preview(nid)
            else:
                open_note_editor(nid)
            return (True, None)
    return handled

# ---------------- 菜单注册 ----------------

def refresh_links_html():
    """按字段里已有的笔记 ID 重新生成链接 HTML (更新样式/快照, 不重算关联)。"""
    fname = field_name()
    nids = mw.col.find_notes('"%s:*notelinker:open:*"' % fname)
    if not nids:
        showInfo("没有找到含链接的笔记。")
        return
    updated = 0
    for nid in nids:
        note = mw.col.get_note(nid)
        ids = get_link_ids(note)
        if ids and set_links(note, ids):
            mw.col.update_note(note)
            updated += 1
    mw.reset()
    tooltip("已刷新 %d 条笔记的链接样式 (v%s 列表版)" % (updated, __version__))


def setup_menu():
    menu = QMenu("Note Linker 卡片关联 v%s" % __version__, mw)
    mw.form.menuTools.addMenu(menu)

    a3 = menu.addAction("🧠 LLM 主题关联 (DeepSeek)…")
    qconnect(a3.triggered, llm_topic_link)

    a4 = menu.addAction("🔄 刷新链接样式/快照")
    a4.setShortcut(QKeySequence("Ctrl+Shift+L"))
    qconnect(a4.triggered, refresh_links_html)


setup_menu()
gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu)
gui_hooks.webview_did_receive_js_message.append(on_js_message)
gui_hooks.profile_will_close.append(_close_plugin_windows)

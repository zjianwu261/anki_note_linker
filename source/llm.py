# -*- coding: utf-8 -*-
"""DeepSeek / OpenAI 兼容接口的 LLM 主题聚类。

只用标准库 urllib, 不依赖第三方包。
"""

import json
import time
import urllib.error
import urllib.request

ASSIGN_SYSTEM = (
    "你是知识管理助手。用户会给出一批学习卡片(编号+内容摘要)。"
    "请给每张卡片分配一个主题名: 2~6 个汉字(或简短英文), 概括卡片所属的知识主题。"
    "粒度适中: 不要宽泛到'医学'这种大类, 也不要具体到每张卡片一个主题。"
    "同一主题的卡片必须使用完全相同的主题名。如果提供了已有主题列表, 优先复用。"
    '只输出 JSON, 格式: {"topics": {"编号": "主题名", ...}}, 编号必须与输入一致, 每张卡片都要有主题。'
)

MERGE_SYSTEM = (
    "你是知识管理助手。下面是一些主题名, 其中可能存在含义相同或高度重叠的。"
    '请把应当合并的主题映射到统一的名称, 只输出 JSON: {"merge": {"旧主题": "统一后主题", ...}}。'
    '没有需要合并的就输出 {"merge": {}}。不要合并含义不同的主题。'
)


class ContentRiskError(RuntimeError):
    """DeepSeek 内容审核拒绝 (HTTP 400 Content Exists Risk)。"""
    pass


def _clean_topic(t):
    return t.strip().replace(" ", "_").replace('"', "").strip("_")[:24]


def chat(cfg, messages, json_mode=True, timeout=300):
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.get("llm_model", "deepseek-v4-flash"),
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
    }
    if "deepseek" in cfg["api_base"]:
        body["thinking"] = {"type": "disabled"}  # 分类任务无需思考模式, 更快更省
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg.get("api_key", ""),
        },
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            if e.code == 400 and ("Content Exists Risk" in detail
                                  or "content_filter" in detail):
                raise ContentRiskError(detail)
            last_err = RuntimeError("API 错误 HTTP %s: %s" % (e.code, detail))
            if e.code in (429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            raise last_err
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise last_err


def _parse_json(content):
    """容错解析: 有些模型会包 ```json ... ``` 代码块。"""
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start:end + 1]
    return json.loads(content)


def assign_topics(cfg, items, progress_cb=None):
    """items: [(note_id, snippet_text), ...] -> ({note_id: 主题名}, [被跳过的 note_id])

    分批调用 LLM。主题列表是全局的: 每批都会把已见主题传给模型要求复用,
    最终分组建链在所有批次汇总后进行, 因此跨批次的卡片同样会互相关联。
    某批触发内容审核时, 对半拆分重试, 只跳过确实被拒的单张卡片。
    """
    batch_size = max(int(cfg.get("llm_batch_size", 40)), 5)
    max_chars = max(int(cfg.get("llm_snippet_chars", 120)), 30)
    topics_seen = []
    result = {}
    skipped = []
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    for bi, batch in enumerate(batches):
        if progress_cb:
            progress_cb(bi, len(batches))
        _assign_batch(cfg, batch, max_chars, topics_seen, result, skipped)
    return result, skipped


def _assign_batch(cfg, batch, max_chars, topics_seen, result, skipped):
    if not batch:
        return
    index = {}
    lines = []
    for i, (nid, text) in enumerate(batch, 1):
        index[str(i)] = nid
        snippet = " ".join(text.split())[:max_chars]
        lines.append("%d. %s" % (i, snippet))
    user = ""
    if topics_seen:
        user += "已有主题列表(优先复用): " + "、".join(topics_seen[:100]) + "\n\n"
    user += "卡片:\n" + "\n".join(lines)
    try:
        content = chat(cfg, [
            {"role": "system", "content": ASSIGN_SYSTEM},
            {"role": "user", "content": user},
        ])
    except ContentRiskError:
        # 内容审核拒绝: 对半拆分, 定位并只跳过被拒的卡片
        if len(batch) == 1:
            skipped.append(batch[0][0])
            return
        mid = len(batch) // 2
        _assign_batch(cfg, batch[:mid], max_chars, topics_seen, result, skipped)
        _assign_batch(cfg, batch[mid:], max_chars, topics_seen, result, skipped)
        return
    try:
        data = _parse_json(content)
    except Exception:
        return  # 这一批解析失败, 跳过
    mapping = data.get("topics", data)
    if not isinstance(mapping, dict):
        return
    for key, topic in mapping.items():
        nid = index.get(str(key).strip())
        if nid is None or not isinstance(topic, str):
            continue
        topic = _clean_topic(topic)
        if not topic:
            continue
        result[nid] = topic
        if topic not in topics_seen:
            topics_seen.append(topic)


def merge_topics(cfg, topics):
    """主题较多时做一次合并去重。返回 {旧主题: 新主题}。"""
    if len(topics) < 8:
        return {}
    try:
        content = chat(cfg, [
            {"role": "system", "content": MERGE_SYSTEM},
            {"role": "user", "content": "主题列表: " + "、".join(topics)},
        ])
        data = _parse_json(content)
        merge = data.get("merge", {})
        if not isinstance(merge, dict):
            return {}
        out = {}
        for k, v in merge.items():
            if isinstance(k, str) and isinstance(v, str):
                v = _clean_topic(v)
                if v and k != v:
                    out[k] = v
        return out
    except Exception:
        return {}  # 合并是锦上添花, 失败不影响主流程

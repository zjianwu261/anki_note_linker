# -*- coding: utf-8 -*-
"""TF-IDF 语义相似度计算 (纯 Python, 无外部依赖, 支持中英文)。"""

import math
import re
from collections import Counter, defaultdict

_HTML_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[A-Za-z0-9_']+")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


def strip_html(text):
    text = re.sub(r"\[sound:[^\]]+\]", " ", text)
    return _HTML_RE.sub(" ", text)


def tokenize(text):
    """英文按单词, 中文按单字 + 相邻双字 (bigram)。"""
    text = strip_html(text).lower()
    tokens = _WORD_RE.findall(text)
    cjk = _CJK_RE.findall(text)
    tokens += cjk
    tokens += [a + b for a, b in zip(cjk, cjk[1:])]
    return tokens


def build_vectors(docs):
    """docs: {note_id: text} -> {note_id: 归一化 TF-IDF 稀疏向量}"""
    tf = {}
    df = Counter()
    for nid, text in docs.items():
        counts = Counter(tokenize(text))
        tf[nid] = counts
        for tok in counts:
            df[tok] += 1
    n = max(len(docs), 1)
    vecs = {}
    for nid, counts in tf.items():
        vec = {}
        for tok, cnt in counts.items():
            idf = math.log((n + 1) / (df[tok] + 1)) + 1.0
            vec[tok] = cnt * idf
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        vecs[nid] = {t: w / norm for t, w in vec.items()}
    return vecs


def cosine_of(a, b):
    """两个归一化稀疏向量的余弦相似度。"""
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def similar_map(vecs, threshold=0.28, max_links=5, progress_cb=None):
    """返回 {note_id: [(other_id, score), ...]} (按分数降序, 每条最多 max_links 个)。

    用倒排索引累加点积, 避免完整 O(n^2)。
    """
    n = len(vecs)
    if n < 2:
        return {}
    inverted = defaultdict(list)
    for nid, vec in vecs.items():
        for tok, w in vec.items():
            inverted[tok].append((nid, w))

    scores = defaultdict(float)
    common_cap = max(n * 0.5, 2)  # 出现在超过一半文档里的词不提供区分度
    done = 0
    total = len(inverted)
    for tok, postings in inverted.items():
        done += 1
        if progress_cb and done % 2000 == 0:
            progress_cb(done, total)
        m = len(postings)
        if m < 2 or m > common_cap:
            continue
        for i in range(m):
            a, wa = postings[i]
            for j in range(i + 1, m):
                b, wb = postings[j]
                key = (a, b) if a < b else (b, a)
                scores[key] += wa * wb

    result = defaultdict(list)
    for (a, b), s in scores.items():
        if s >= threshold:
            result[a].append((b, s))
            result[b].append((a, s))
    for nid in list(result.keys()):
        result[nid].sort(key=lambda x: -x[1])
        result[nid] = result[nid][:max_links]
    return dict(result)

# -*- coding: utf-8 -*-
"""DeepSeek 云端接口调用（视觉 deepseek-flash），仅用标准库 urllib。"""
import base64
import json
import re
import urllib.error
import urllib.request

import db

API_URL = "https://api.deepseek.com/chat/completions"
TIMEOUT = 180


class AIError(Exception):
    pass


def _api_key():
    cfg = db.load_config()
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise AIError("尚未配置 DeepSeek API 密钥，请到「设置」中填写。")
    return key


def _post(payload, timeout=TIMEOUT):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + _api_key(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        if e.code == 401:
            raise AIError("API 密钥无效或已过期，请到「设置」中检查。")
        raise AIError(f"DeepSeek 接口返回错误（HTTP {e.code}）：{body}")
    except urllib.error.URLError as e:
        raise AIError(f"网络连接失败，无法访问 DeepSeek 接口：{e.reason}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise AIError("DeepSeek 返回了无法解析的内容：" + json.dumps(data, ensure_ascii=False)[:300])


def encode_image(data_url):
    """data URL -> base64 字符串。"""
    m = re.match(r"^data:image/[\w+.-]+;base64,(.*)$", data_url, re.S)
    if m:
        return m.group(1)
    return base64.b64encode(data_url.encode("utf-8")).decode("ascii")


def _vision_messages(prompt, image_data_urls):
    content = [{"type": "text", "text": prompt}]
    for u in image_data_urls:
        content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + encode_image(u)},
        })
    return [{"role": "user", "content": content}]


def _extract_json(text):
    """从模型回复中提取第一个 JSON 对象/数组。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    # 找第一个 { 或 [
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == open_c:
                    depth += 1
                elif ch == close_c:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:
                            break
        return None
    return None


EXTRACT_PROMPT = """你是机械制图审图员。这是一页机械零件图/装配图（PDF转的图片，可能有多张图片=多页）。
请完整读图，输出严格 JSON（不要任何多余文字、不要 markdown 代码块标记），结构如下：
{
 "title": "图纸名称（标题栏“名称/图名”）",
 "drawing_no": "图号（标题栏“图号/代号”）",
 "material": "材料牌号（标题栏“材料”栏，没有则为空串）",
 "scale": "比例（如 1:1）",
 "qty": "数量（标题栏，没有则为空串）",
 "version": "版本/标记（标题栏，没有为空串）",
 "draw_date": "日期（标题栏，格式 YYYY-MM-DD，读不出则空串）",
 "category": "零件图 或 装配图（根据标题栏或图面内容判断）",
 "dimensions": [{"name":"特征名，如 φ50外圆","value":"尺寸值","tolerance":"公差/极限偏差，无则空串"}],
 "fits": [{"name":"配合特征","value":"配合代号如 H7/g6","tolerance":""}],
 "roughness": ["表面粗糙度要求，如 Ra1.6（去除材料）"],
 "geometric_tolerances": [{"feature":"被测要素","symbol":"形位公差符号","value":"公差值","datum":"基准，无则空串"}],
 "heat_treatment": "热处理要求，无则空串",
 "hardness": "硬度要求，无则空串",
 "unspecified_tolerance": "未注公差标准，无则空串",
 "technical_requirements": ["技术要求逐条，没有则空数组"],
 "bom": [{"no":"序号","name":"零件名称","material":"材料","qty":"数量"}],
 "notes": "其它对加工/检验有影响的信息，无则空串"
}
要求：
1. 必须忠实于图面文字，不得编造；读不清的字段填空串并在 notes 里注明“某字段模糊”。
2. 尺寸只列有公差要求或配合要求的以及主要特征尺寸，最多 40 条。
3. 装配图才填 bom，零件图 bom 用空数组。
4. 只输出 JSON。"""

AUDIT_PROMPT = """你是机械制图审核工程师。这是一页机械零件图/装配图（可能多张图片=多页），请按国标机械制图规范做勘误检查。
输出严格 JSON（不要多余文字、不要 markdown 标记）：
{
 "issues": [
   {"severity":"severe|general|hint","category":"分类见下","page":页码,"location":"问题位置描述，如“标题栏下方/尺寸Φ30处”",
    "problem":"问题描述","suggestion":"建议如何修改","basis":"依据，如 GB/T 4458.4-2003 或常规做法"}
 ]
}
severity 含义：severe=严重（会导致加工错误/无法生产，如关键尺寸缺失、公差矛盾、配合代号错误、剖面与标注矛盾）；
general=一般（不符合规范但不影响生产理解，如粗糙度漏标、图线不规范、标注风格不统一）；hint=提示（建议改进）。
检查类别（category 用这些词）：尺寸标注、公差配合、表面粗糙度、形位公差、材料与热处理、技术要求、标题栏完整性、图面一致性、投影与剖视、明细表。
检查要点：
1. 有配合/加工要求的尺寸是否标了公差或配合代号；表面粗糙度是否覆盖所有加工面；
2. 形位公差基准是否明确、被测要素是否可测；公差数值是否与尺寸/配合矛盾；
3. 标题栏关键项（图号、名称、材料、比例、日期、数量）是否缺失；
4. 技术要求与图面标注是否矛盾、是否缺必要要求（去毛刺、未注圆角等）；
5. 尺寸是否封闭/重复/与视图矛盾；装配图明细表与图中零件序号是否一致。
没问题就返回 {"issues": []}。只输出 JSON。"""


def test_key():
    """用一次极小的文本调用验证密钥可用。"""
    reply = _post({
        "model": db.load_config().get("model", "deepseek-flash"),
        "messages": [{"role": "user", "content": "只回复两个字：正常"}],
        "max_tokens": 10,
        "temperature": 0,
    }, timeout=30)
    return reply.strip()[:20]


def extract_params(image_data_urls):
    reply = _post({
        "model": db.load_config().get("model", "deepseek-flash"),
        "messages": _vision_messages(EXTRACT_PROMPT, image_data_urls),
        "temperature": 0.1,
    })
    data = _extract_json(reply)
    if not isinstance(data, dict):
        raise AIError("AI 提取结果无法解析为结构化数据，请重试。")
    return data


def audit_drawing(image_data_urls):
    reply = _post({
        "model": db.load_config().get("model", "deepseek-flash"),
        "messages": _vision_messages(AUDIT_PROMPT, image_data_urls),
        "temperature": 0.1,
    })
    data = _extract_json(reply)
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return data["issues"]
    if isinstance(data, list):
        return data
    raise AIError("AI 勘误结果无法解析，请重试。")


def smart_query(question, digest):
    """自然语言查询：把台账摘要交给文本模型检索作答。"""
    system = (
        "你是工程图纸库的查询助手。下面给出图纸库台账摘要（JSON 数组）。"
        "根据用户问题在其中检索，回答用简体中文、口语化、先给结论再给依据。"
        "命中具体图纸时，必须在答案最后一行输出一行 JSON："
        '{"hits":[图纸id列表]}，没命中就 {"hits":[]}。不要输出其他 JSON。'
    )
    digest_text = json.dumps(digest, ensure_ascii=False)
    if len(digest_text) > 12000:
        digest_text = digest_text[:12000] + "…(截断)"
    reply = _post({
        "model": db.load_config().get("model", "deepseek-flash"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "台账摘要：\n" + digest_text + "\n\n问题：" + question},
        ],
        "temperature": 0.2,
    })
    hits = []
    m = re.search(r"\{[^{}]*\"hits\"\s*:\s*\[[^\]]*\][^{}]*\}", reply)
    if m:
        try:
            hits = json.loads(m.group(0)).get("hits", [])
        except Exception:
            hits = []
        reply = reply.replace(m.group(0), "").strip()
    hits = [h for h in hits if isinstance(h, int) or (isinstance(h, str) and h.isdigit())]
    return reply.strip(), [int(h) for h in hits]

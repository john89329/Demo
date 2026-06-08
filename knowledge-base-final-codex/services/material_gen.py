import re

from config import DEEPSEEK_API_KEY, TOP_K_RESULTS
from services.vector_store import get_store
from services.llm_client import chat_completion


def parse_framework(text):
    lines = text.strip().split("\n")
    sections = []
    current_section = None
    current_sub = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        m = re.match(r'^(\d+(?:\.\d+)*)[.、\s]\s*(.*)', stripped)
        if m:
            num = m.group(1)
            title = m.group(2).strip()
            depth = num.count(".") + 1 if "." in num else 1

            entry = {
                "number": num,
                "title": title,
                "depth": depth,
                "content": "",
                "subsections": [],
            }

            if depth == 1:
                current_section = entry
                sections.append(current_section)
                current_sub = None
            elif depth == 2 and current_section:
                current_sub = entry
                current_section["subsections"].append(current_sub)
            elif depth >= 3 and current_sub:
                current_sub["subsections"].append(entry)
            else:
                sections.append(entry)
                current_section = entry
                current_sub = None
        else:
            if current_section:
                parts = current_section.get("extra", [])
                parts.append(stripped)
                current_section["extra"] = parts

    return sections


def generate_material(framework_text, api_key=DEEPSEEK_API_KEY, embed_key=None, kb_name=None):
    if not api_key:
        return {"error": "请先在设置中配置 DeepSeek API 密钥。"}

    sections = parse_framework(framework_text)
    if not sections:
        return {"error": "未能解析框架，请检查格式。"}

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    emb_key = embed_key or api_key
    total = len(sections)
    results = []

    for i, section in enumerate(sections):
        query = f"{section['title']}"
        try:
            search_results = store.search(query, emb_key, TOP_K_RESULTS)
        except Exception:
            search_results = store.keyword_search(query, TOP_K_RESULTS)

        context_parts = []
        sources = []
        seen = set()
        for chunk, score in search_results:
            fname = chunk.get("source_file", "").split("\\")[-1].split("/")[-1]
            context_parts.append(f"[Source: {fname}]\n{chunk['text'][:1000]}")
            key = chunk.get("source_file", "")
            if key not in seen:
                seen.add(key)
                sources.append({"file": fname, "file_path": key, "score": round(score, 3)})

        context = "\n\n".join(context_parts) if context_parts else "无相关上下文。"
        desc = section.get("description", "")
        extra = "\n".join(section.get("extra", [])) if section.get("extra") else ""

        sub_titles = [s["title"] for s in section.get("subsections", [])]
        sub_hint = f"\n子章节：{', '.join(sub_titles)}" if sub_titles else ""

        prompt = f"""你正在生成一份审核/核实材料。请根据以下章节标题和知识库内容，撰写该章节的详细内容。

章节标题：{section['title']}
章节编号：{section['number']}
{desc}
{extra}{sub_hint}

知识库参考内容：
{context}

要求：
1. 内容应专业、详实，基于知识库中的信息
2. 引用来源时使用 [Source: 文件名] 格式
3. 如知识库信息不足，应根据常识补充合理内容
4. 使用中文撰写
5. 字数不少于 300 字"""

        content = chat_completion(
            prompt=prompt, api_key=api_key, temperature=0.5,
            timeout=180,
            system_prompt="你是一个专业的文档编写助手，擅长根据大纲和参考资料撰写审核材料。",
        )
        if not content:
            content = f"（{section['title']} 内容生成失败）"

        results.append({
            "number": section["number"],
            "title": section["title"],
            "depth": section["depth"],
            "content": content,
            "sources": sources,
            "subsections": section.get("subsections", []),
        })

    full_text = _assemble_document(results)

    polish_prompt = (
        "请对以下文档进行整体润色，确保语言流畅、逻辑连贯、格式统一。直接输出润色后的完整文档。\n\n"
        + full_text
    )
    polished = chat_completion(
        prompt=polish_prompt, api_key=api_key, temperature=0.5, timeout=180,
        system_prompt="你是一个专业的文档编写助手，擅长根据大纲和参考资料撰写审核材料。",
    )
    final_text = polished if polished else full_text

    return {
        "sections": results,
        "full_text": final_text,
    }


def _assemble_document(sections):
    parts = ["# 审核/核实材料\n"]
    for sec in sections:
        prefix = "#" * min(sec["depth"] + 1, 6)
        parts.append(f"\n{prefix} {sec['number']} {sec['title']}\n")
        parts.append(f"\n{sec['content']}\n")
    return "\n".join(parts)


TEMPLATES = {
    "质量管理审核": """1. 质量管理体系
  1.1 质量方针与目标
  1.2 质量管理体系文件
  1.3 文件控制程序
2. 管理职责
  2.1 管理承诺
  2.2 以顾客为关注焦点
  2.3 质量方针
  2.4 策划
3. 资源管理
  3.1 人力资源
  3.2 基础设施
  3.3 工作环境
4. 产品实现
  4.1 产品实现的策划
  4.2 与顾客有关的过程
  4.3 设计和开发
  4.4 采购
  4.5 生产和服务提供
5. 测量分析与改进
  5.1 监视和测量
  5.2 不合格品控制
  5.3 数据分析
  5.4 改进""",

    "合规性检查清单": """1. 法律法规合规性
  1.1 营业执照与许可
  1.2 行业资质
  1.3 知识产权
2. 安全生产
  2.1 安全管理制度
  2.2 安全培训记录
  2.3 应急预案
3. 环境保护
  3.1 环评批复
  3.2 污染物排放
  3.3 废弃物处理
4. 劳动用工
  4.1 劳动合同
  4.2 社会保险
  4.3 劳动保护""",

    "项目文档审查": """1. 项目立项
  1.1 立项申请
  1.2 可行性研究报告
  1.3 项目计划书
2. 项目执行
  2.1 进度报告
  2.2 变更管理
  2.3 风险管理
3. 项目验收
  3.1 验收报告
  3.2 测试报告
  3.3 用户反馈
4. 项目归档
  4.1 文档清单
  4.2 技术资料
  4.3 总结报告""",
}

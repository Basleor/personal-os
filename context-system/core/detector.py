"""
Personal OS — Layer 01: 任务类型检测器 (编程/内容/文档)
External drive: /Volumes/13384923891/hermes-agent/
Seed — full implementation pending v2.2+ evolution.
"""

def detect_task_type(user_message: str) -> str:
    """
    Detect the task type from user input.
    Seed implementation — placeholder classification.
    """
    msg_lower = user_message.lower()

    code_keywords = ['code', 'function', 'class', 'bug', 'test', 'refactor',
                     'import', 'python', 'javascript', 'go ', 'rust',
                     '写代码', '修复', '调试', '重构']
    content_keywords = ['article', 'blog', 'write', 'draft', 'content',
                        '文章', '写作', '文案', '报告']
    doc_keywords = ['document', 'readme', 'spec', 'requirement',
                    '文档', '说明', '规范']

    score_code = sum(1 for kw in code_keywords if kw in msg_lower)
    score_content = sum(1 for kw in content_keywords if kw in msg_lower)
    score_doc = sum(1 for kw in doc_keywords if kw in msg_lower)

    if score_code >= score_content and score_code >= score_doc and score_code > 0:
        return "💻 编程"
    elif score_content >= score_code and score_content >= score_doc and score_content > 0:
        return "📝 内容"
    elif score_doc > 0:
        return "📄 文档"
    return "📋 通用"

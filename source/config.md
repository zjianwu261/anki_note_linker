# Note Linker 配置说明

## 基础

- `field_name`: 存放关联链接的字段名,默认 `相关卡片`。改名后旧字段不会自动迁移。
- `similarity_threshold`: TF-IDF 自动关联的相似度阈值 (0~1),越高越严格。默认 0.28。
- `max_links_per_note`: 每张卡片自动关联的最大数量。默认 5。
- `search_query`: 自动关联时默认的搜索范围 (Anki 搜索语法),留空表示全部笔记。
- `add_to_template`: 添加字段时是否自动把 `{{相关卡片}}` 加到卡片背面模板。默认 true。
- `exclude_fields`: 计算相似度/发送给 LLM 时忽略的字段名列表。
- `link_title_chars`: "相关卡片"链接快照的最大长度 (字符),默认 120。超长部分折叠,点击"…展开"可显示全部 (上限 500 字)。修改后用菜单"🔄 刷新链接样式/快照"应用到已有链接。

## LLM 主题关联 (DeepSeek)

- `api_base`: OpenAI 兼容接口地址,默认 `https://api.deepseek.com`。也可以换成其他兼容服务 (如 Ollama 本地 `http://localhost:11434/v1`)。
- `api_key`: API 密钥,在 platform.deepseek.com 获取。首次使用时也会弹窗询问。
- `llm_model`: 模型名,默认 `deepseek-v4-flash` (便宜快速)。
- `llm_batch_size`: 每次请求发送的卡片数,默认 40。
- `llm_snippet_chars`: 每张卡片发送的摘要长度 (字符),默认 120。越长分类越准但费用越高。
- `topic_tag_prefix`: 主题标签前缀,默认 `NL::`,生成的标签形如 `NL::心脏解剖`。重新运行时旧的前缀标签会被替换。
- `llm_selected_decks`: 上次勾选的牌组,运行后自动记录,下次打开对话框时默认勾上。一般不需要手动改。

范围不再手动输入搜索语法:点菜单后会弹出牌组树,勾选要参与分类的牌组即可(勾选父牌组自动包含子牌组)。

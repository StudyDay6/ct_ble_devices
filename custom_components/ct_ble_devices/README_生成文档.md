# 生成 Word 文档说明

## 使用方法

### 方法 1: 使用虚拟环境（推荐）

```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate  # macOS/Linux
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install python-docx

# 运行脚本
python3 generate_docx.py
```

### 方法 2: 使用 pipx

```bash
# 安装 pipx（如果还没有）
brew install pipx

# 安装 python-docx
pipx install python-docx

# 运行脚本
python3 generate_docx.py
```

### 方法 3: 直接安装（需要管理员权限）

```bash
pip3 install --break-system-packages python-docx
python3 generate_docx.py
```

## 输出

运行脚本后，会在当前目录生成 `验证报告.docx` 文件。

## 注意事项

- 确保 `验证报告.md` 文件在同一目录下
- 生成的 Word 文档会保留 Markdown 的格式（标题、表格、列表等）
- 如果遇到编码问题，确保 Markdown 文件使用 UTF-8 编码


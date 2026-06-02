# PicPost

> 摄影博主的小红书发图工作流工具 — 拍完就发，一站搞定。

**PicPost** 覆盖了从选片到发布的完整流程：批量水印/调色/滤镜、AI 智能选图、风格包一键套用、实时预览、九宫格/长图/对比图/封面卡片、防盗水印、智能裁图、AI 文案标签、发布草稿打包。支持 CLI 命令行和 Streamlit Web 两种使用方式。

## 效果展示

||||
|-|-|-|
|![](images/1.jpeg)|![](images/2.jpeg)|![](images/3.jpeg)|
|![](images/4.jpeg)|![](images/5.jpeg)|![](images/6.jpeg)|
|![](images/7.jpeg)|![](images/8.jpeg)|![](images/9.jpeg)|

## 核心功能

- **批量水印** — 读取 EXIF 自动生成相机/镜头/参数/日期水印，10+ 种布局可选
- **调色滤镜** — 内置清新/胶片/暖调/冷调/Ins/黑白预设 + 手动亮度/对比度/饱和度/锐化/色温
- **选片打星** — 网格缩略图浏览、打星、过滤，AI 智能从几十张里挑出最适合发的几张
- **全维度风格包** — 把布局+调色+水印+文字一整套保存/导入导出/一键套用，保持账号统一风格
- **实时预览** — 调参数即时看到效果，不用每次点"处理"
- **智能裁图** — 裁成 3:4/1:1/9:16 时自动避开主体/人脸（纯 PIL 显著性 + 可选 OpenCV）
- **小红书工具箱** — 九宫格切图、长图拼接、前后对比、拼图封面、首图文字卡片、页码角标、多比例导出、文字标注
- **AI 文案标签** — 看图生成标题/正文/话题标签，支持多候选对比（OpenAI / Gemini）
- **AI 风格建议** — 看图推荐适配滤镜 + 标签，一键采纳
- **封面单独样式** — 封面用吸睛风格、内页用统一风格
- **发布草稿打包** — 按顺序命名图片 + 文案打成 ZIP，直接按序上传
- **隐私保护** — 一键清除 GPS / 全部 EXIF，发图不泄露位置
- **防盗水印** — 半透明文字整图平铺或单点
- **视频合成** — 输出图片一键合成轮播视频（需 ffmpeg）

## 快速开始

### 安装

```bash
git clone https://github.com/xyy2wy/picpost.git ~/picpost
cd ~/picpost
pip3 install -r requirements.txt
chmod +x scripts/install.sh && ./scripts/install.sh
```

### 命令行模式

```bash
python3 main.py
```

把图片放入 `input/`，按菜单调整参数，输入 `y` 开始处理，结果输出到 `output/`。

### Web 前端模式

```bash
streamlit run web_app.py
```

浏览器打开 `http://localhost:8501`，左侧调参数、中间选图处理、实时预览效果。

## 依赖

| 包 | 用途 |
|---|---|
| Pillow | 图片处理核心 |
| PyYAML | 配置读写 |
| tqdm | CLI 进度条 |
| python-dateutil | 日期解析 |
| requests | 网络请求 |
| streamlit | Web 前端 |
| openai | AI 功能（兼容 OpenAI / Gemini） |
| opencv-python (可选) | 智能裁图人脸检测，不装也能用 |
| ExifTool (外部) | EXIF 读写 |

## 配置

通过 `config.yaml` 配置所有参数。Web 端左侧边栏可视化调整，CLI 通过菜单修改。

详细说明见 [docs/使用文档.md](docs/使用文档.md)。

## 布局预览

| 布局 | 效果 |
|---|---|
| normal | ![](images/1.jpeg) |
| normal(Logo 居右) | ![](images/2.jpeg) |
| 黑红配色 | ![](images/3.jpeg) |
| 简洁 | ![](images/7.jpeg) |
| 背景模糊 | ![](images/8.jpeg) |
| 白色边框 | ![](images/9.jpeg) |

## 项目结构

```
picpost/
├── main.py                      # CLI 入口
├── web_app.py                   # Web 前端入口（Streamlit）
├── config.yaml                  # 用户配置
├── requirements.txt             # Python 依赖
│
├── core/                        # 核心层：数据模型与处理器
│   ├── constants.py             #   常量定义（元素名称/值、状态枚举）
│   ├── config.py                #   Config 类：读写 config.yaml
│   ├── container.py             #   ImageContainer：封装图片 + EXIF
│   └── processors.py            #   处理器链（Composite 模式，10+ 布局）
│
├── services/                    # 服务层：纯函数业务逻辑
│   ├── processing.py            #   处理管线：构建处理链、批量处理
│   ├── color.py                 #   调色 / 滤镜 / 自动增强 / 防盗水印
│   ├── annotation.py            #   文字贴纸 / 图上标注
│   ├── compose.py               #   长图拼接 / 前后对比
│   ├── cover.py                 #   首图文字卡片
│   ├── xiaohongshu.py           #   切图 / 拼图 / 页码 / 多比例导出
│   ├── smart_crop.py            #   智能裁切（人脸优先 / 显著性）
│   ├── selection.py             #   选片（缩略图、打星、过滤、导出）
│   ├── publish.py               #   发布草稿打包
│   └── video.py                 #   FFmpeg 视频合成
│
├── ai/                          # AI 能力层（可选，需 openai 包）
│   ├── preset.py                #   AI 预设建议 / 选图 / 文案 / 风格标签
│   └── style_pack.py            #   风格包 schema / 序列化 / 校验
│
├── cli/                         # CLI 交互层
│   ├── menu.py                  #   菜单组件系统（Composite 模式）
│   ├── setup.py                 #   配置加载、菜单组装
│   └── xiaohongshu.py           #   小红书工具 CLI 交互
│
├── web/                         # Web 前端层（Streamlit）
│   ├── app.py                   #   主应用
│   └── preview.py               #   实时预览生成
│
├── utils_pkg/                   # 工具层
│   └── helpers.py               #   ExifTool、图片缩放/拼接/裁切
│
├── scripts/                     # 构建 / 安装脚本
│   ├── install.sh               #   初始化安装
│   ├── main.spec                #   PyInstaller 打包
│   └── build_win_pkg.spec       #   Windows 发布包
│
├── fonts/                       # 字体资源
├── logos/                       # Logo 资源
├── docs/                        # 文档
├── input/                       # 输入图片
└── output/                      # 输出图片
```

## 许可证

基于 [Apache License 2.0](LICENSE) 发布。

本项目基于 [semi-utils](https://github.com/leslievan/semi-utils) 二次开发，感谢原作者 [@LeslieVan](https://github.com/leslievan) 的工作。

引用了 [ExifTool](https://exiftool.org/)（GPL v1 + Artistic License 2.0）。

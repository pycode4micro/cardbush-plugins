# 导出参数

`garment_present` 接受以下参数。`revision` 必填，使用真实读取的版本，不能写“latest”。

```json
{
  "project_id": "实际项目 ID",
  "revision": 3,
  "formats": ["pdf", "pptx", "html"],
  "language": "zh",
  "title": "雾蓝短外套设计方案",
  "slides": [
    {
      "title": "短衣身与非对称翻领",
      "visual": "overview",
      "bullets": ["短衣身形成上提的视觉比例", "雾蓝作为主体配色"],
      "notes": "结合实际保存的款式，先讲整体轮廓，再讲非对称细节。"
    },
    {
      "title": "领口细节",
      "visual": "part",
      "part_id": "实际领口 ID",
      "bullets": ["这里填写当前领口真实的设计要点"],
      "notes": "这里填写适合口头补充的解释，不编造未设计的结构。"
    },
    {"title": "配色", "visual": "palette", "bullets": []}
  ]
}
```

- `formats` 可任意选取 `pdf`、`pptx`、`html`；默认全部。`language` 为 `zh` / `en`，决定界面标签，正文由你按用户要求写。
- `slides` 可省略，自动依据该版的标题、描述、可见部位生成快稿。自动摘要会精简屏幕文字，完整解释应放进备注；正式提案建议自行组织大纲。
- 每页 `visual` 选 `overview` / `front` / `back` / `part` / `palette`。`part` 必须提供存在且可见的 `part_id`；其他模式不传。没有背面设计时不能请求背面页。
- 每页标题最多 60 字符，要点最多 5 条、每条 120 字符、合计 280 字符；备注最多 4,000 字符。整份最多 24 页。密集内容用备注或拆页，不靠把字缩到看不清。
- `title` 为交付文件内部标题，不改变项目名称。导出不会创建新设计版本。相同内容复用已完成文件，不覆盖其他方案。
- 返回 `files` 列出成功格式，`errors` 列出失败格式及原因，`vectors` 是单独 SVG，`outline` 是可复用讲解大纲；它们全部位于插件所在主机。

## 命令行后备

使用实际找到的插件 / Skill 绝对路径，下面路径仅为说明位置：

```sh
node <skill-dir>/scripts/export.mjs --project-id PROJECT_ID --revision 3 --formats pdf,pptx,html
```

可选 `--outline <absolute-plan.json>` 传与上面相同结构的大纲；`--data-dir` 指向已有设计根目录，`--output-dir` 指向目标目录，`--font` 指向已安装且覆盖所需字符的 TTF/OTF/TTC 字体。所有路径属于当前执行主机。脚本 stdout 返回 JSON，任何格式失败时退出码为 1，成功文件仍保留。

格式实现参考：[PDFKit 字体](https://pdfkit.org/docs/text.html)、[SVG-to-PDFKit](https://github.com/alafr/SVG-to-PDFKit)、[PptxGenJS SVG 支持](https://gitbrent.github.io/PptxGenJS/docs/api-images/)。

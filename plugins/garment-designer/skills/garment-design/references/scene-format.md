# 矢量设计格式

`garment_project create` 可传 `template`（shirt / jacket / dress / trousers）或完整 `scene`。模板不限于列出的品类；自定义场景用于任意款式。

```json
{
  "title": "不对称短外套",
  "brief": "日常穿着，有一点雕塑感",
  "globalPrompt": "柔和雾蓝色，挺括而非硬塑料质感",
  "negativePrompt": "不加品牌标志，不增加口袋",
  "width": 800,
  "height": 1000,
  "parts": [{
    "id": "body-front",
    "name": "正面衣身",
    "view": "front",
    "kind": "body",
    "fill": "#cadbe3",
    "stroke": "#333333",
    "strokeWidth": 2,
    "z": 1,
    "prompt": "微宽松的短衣身，右侧下摆略低",
    "requirements": ["前襟不设纽扣"],
    "locked": false,
    "elements": [{"type":"path","d":"M250 190 L350 150 Q400 195 450 150 L550 190 L550 660 L250 620 Z"}]
  }]
}
```

这只是格式示例，不是完整款式；添加领口、袖子等实际需要的部位。描线一般 1–3 单位，不要全部画成粗边卡通。

## 图元与边界

- `path`: `d` 支持 M/L/H/V/C/S/Q/T/A/Z 与小写相对命令。任意路径可实现不对称剪影、褶线、镂空等；复杂孔洞可以用反向子路径的非零填充规则。
- `rect`: x, y, width, height, 可选 rx。
- `ellipse`: cx, cy, rx, ry。
- `line`: x1, y1, x2, y2。
- `polyline`: points，例如 `[[20,30],[80,90]]`。
- `text`: x, y, text, fontSize。字体使用插件主机上的系统字体，无法保证各平台字形完全相同；复杂印花优先转为路径。
- 每个图元可覆盖 fill、stroke、strokeWidth、opacity。颜色为 `#RGB` / `#RRGGBB` / `none`，不接受 URL、CSS 或外链。
- 每个部位可有 transform `{x,y,rotate,scaleX,scaleY}`；旋转中心为画布原点。复杂转角更适合直接改几何。
- `view` 为 front/back；`visible` 控制显示；`z` 控制同一视图内的叠放顺序。ID 固定，不因改名变化。
- 80 个部位以内，每部位最多 64 图元。画布 200–2000；单份设计最多约 750 KB。复杂印花可以用简化矢量轮廓加精确视觉描述，但明确哪些效果属于最终生图阶段。
- 不接受任意 SVG 源码、滤镜、脚本、外部图片引用。参考照片由独立 reference 工具存储，不伪装成可编辑部位。

## 局部修改

```json
{
  "project_id":"项目 ID",
  "expected_revision":2,
  "patch":{
    "parts":[{
      "id":"collar",
      "prompt":"左侧更宽的尖翻领，细银边",
      "elements":[{"type":"path","d":"M340 145 L395 195 L335 270 L300 180 Z M460 145 L405 195 L450 250 L495 180 Z"}]
    }]
  },
  "note":"放宽左领片，保留右侧比例"
}
```

仅提供的字段改变。`elements` 数组整体替换，先读取原图元再合并；新部位至少提供 id/name/elements。删除使用 `patch.remove`。修改锁定部位必须在用户要求时显式 `patch.unlock:["part-id"]`，且说明受影响部位。历史恢复用 `garment_project restore`，不是覆盖旧文件。

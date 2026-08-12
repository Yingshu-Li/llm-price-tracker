# 第三方数据副本

本目录存放两个 **MIT 许可**开源数据集的本地副本，用于在上游站点不可用时
仍能获得价格数据。由 `python update_prices.py --refresh-vendor` 刷新。

MIT 许可要求在再分发时保留版权与许可声明，以下为此目的：

## modelsdev.json

- 来源：<https://models.dev/api.json>
- 项目：<https://github.com/anomalyco/models.dev>
- 许可：MIT

```
MIT License — Copyright (c) models.dev contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## litellm.json

- 来源：<https://github.com/BerriAI/litellm> · `model_prices_and_context_window.json`
- 许可：MIT（`enterprise/` 目录另有企业许可，本文件不在其中）

```
MIT License — Copyright (c) 2023 Berri AI

（许可全文同上）
```

## 免责

价格数据仅供参考，以各厂商官方定价页为准。

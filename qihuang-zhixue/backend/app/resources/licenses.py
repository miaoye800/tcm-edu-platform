"""素材许可白名单——**唯一来源**。

这里曾经有两份，而且不一致：

- ``app/ppt/select.py``：运行时判定「这张图能不能用」。只有 ``PD-old`` /
  ``CC0`` / ``CC-BY-4.0`` 三种。
- ``tools/assets/audit.py``：入库判定。多出 ``MIT`` / ``Apache-2.0``。

后果是图标（多数是 MIT / Apache-2.0）能通过入库校验写进登记表，选材时却在
``score()`` 里被判 ``-1`` 直接排除——**入库了等于没入**，而且没有任何报错。

所以白名单收敛到这里。这个文件刻意不依赖 pydantic、不 import 任何 app 模块，
好让 ``tools/`` 下的独立脚本按路径加载（``audit.py`` 只有标准库）。

许可的红线（写在这里，免得只存在于某个评审的脑子里）：

- ``CC0`` / ``PD-old``：可商用、免署名。仍建议留 ``source_url`` 以便追溯。
- ``CC-BY-*``：可商用，**必须署名**。缺 ``attribution`` 一律判不合规。
- ``OFL-1.1``：字体许可，允许嵌入与再分发，**必须保留版权声明**。
- ``MIT`` / ``Apache-2.0``：图标集常见。可商用，保留声明。
- ``Pexels-License``：可商用、免署名；**不得转售未修改的原图**，不得暗示
  平台背书，含可识别人像的图不得用于敏感语境。
- ``Heritage-Free``：免费商用插画；按套组条款，**不得单独转售或再配布素材文件**。

**演示期例外**：``CC BY-NC``（pngimg 用这一族）是非商业条款，正常不该收录——
用户可能把课件拿去收费培训，而我们控制不了下游用途。但本项目当前定位是
**比赛演示，不对外提供商业服务**，所以放行，并单独标进 ``DEMO_ONLY_LICENSES``。
这不是「许可问题解决了」，是「记了一笔技术债」：商业化之前必须跑
``tools/assets/audit.py``，它会把这一族许可逐条列出来。
"""

from __future__ import annotations

#: 许可白名单。不在表里的一律拒绝——「来源不明」不是一种许可。
LICENSE_ALLOWLIST: dict[str, str] = {
    "PD-old": "公版（作者逝世逾 100 年 / 出版逾 100 年）",
    "CC0": "公共领域贡献",
    "CC-BY-4.0": "署名 4.0 国际",
    "CC-BY-3.0": "署名 3.0 国际",
    "OFL-1.1": "开源字体许可 1.1（可嵌入、可再分发）",
    "MIT": "MIT（图标集常见）",
    "Apache-2.0": "Apache 2.0（图标集常见）",
    "Pexels-License": "Pexels 免费使用许可（免署名；禁转售未修改原图）",
    "Heritage-Free": "Heritage Type Co. 免费商用插画",
    "CC-BY-NC-4.0": "署名-非商业 4.0（演示期放行；商业化前必须替换）",
}

#: 演示期放行的许可。它们能进库、能被选材，但商业化之前必须清理干净。
#: ``audit.py`` 每次都会把它们数出来并打印，不用人记。
DEMO_ONLY_LICENSES: frozenset[str] = frozenset({"CC-BY-NC-4.0"})

#: 需要署名的许可。缺 ``attribution`` 的资产在 check 里直接判不合规。
LICENSES_NEEDING_ATTRIBUTION: tuple[str, ...] = ("CC-BY-4.0", "CC-BY-3.0", "CC-BY-NC-4.0", "OFL-1.1")

#: 明确排除的许可，附理由，便于报错时说人话。
REJECTED_LICENSES: dict[str, str] = {
    "CC-BY-NC-SA-4.0": "非商业且要求以相同方式共享：会传染到整份课件",
    "Unknown": "来源不明不是一种许可",
}

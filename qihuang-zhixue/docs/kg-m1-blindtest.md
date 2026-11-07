# 契约层盲测结果

- 模型：`deepseek-chat`
- 题数：20
- 输入：`contract.describe_all()` 六段说明 + 问句；**不给源码、不给判据表**

| # | 问句 | 期望 | 实际 | |
|---|---|---|---|---|
| 1 | 麻黄和桂枝为什么常一起用 | ask | ask | ✓ |
| 2 | 人参和藜芦能一起用吗 | ask | ask | ✓ |
| 3 | 麻黄和桂枝有什么区别 | compare | compare | ✓ |
| 4 | 麻黄到麻黄汤中间经过什么 | path | path | ✓ |
| 5 | 麻黄的功效是什么 | none | none | ✓ |
| 6 | 咽喉肿痛用什么药 | lookup | lookup | ✓ |
| 7 | 淮山是不是山药 | lookup | lookup | ✓ |
| 8 | 白术和苍术怎么区分 | compare | compare | ✓ |
| 9 | 麻黄能治哪些证 | ask | ask | ✓ |
| 10 | 麻黄汤由哪些药组成 | ask | ask | ✓ |
| 11 | 丁香和郁金同方会有什么问题 | ask | ask | ✓ |
| 12 | 黄芪和当归为什么要配在一起 | ask | ask | ✓ |
| 13 | 桂枝的用量是多少 | none | none | ✓ |
| 14 | 白芍和赤芍的异同 | compare | compare | ✓ |
| 15 | 生姜和干姜是怎么扯上关系的 | path | path | ✓ |
| 16 | 生地黄和熟地黄哪个更适合阴虚 | compare | compare | ✓ |
| 17 | 附子中毒怎么解救 | none | none | ✓ |
| 18 | 这题我错了，错在哪 | attribute | attribute | ✓ |
| 19 | 处方：人参9g，藜芦3g，有问题吗 | review | review | ✓ |
| 20 | 阿莫西林和中药能一起吃吗 | none | none | ✓ |

**命中 20/20**（门槛 17/20 = 85%）

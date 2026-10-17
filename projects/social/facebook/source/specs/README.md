# Facebook 的历史研发记录怎么看

这个文件夹放的是以前做系统维护时留下的设计、任务分工、测试和复查结果。**它不是安装说明，也不是现在开始抓取 Facebook 的入口。**

需要实际使用程序，先看 [上一级项目 README](../README.md)；想知道某次改动为什么这么做，再来翻这些记录。

这些文件都在 `001-system-maintenance/` 里：

- `spec.md`：当时打算解决哪些问题；
- `plan.md`、`tasks.md`：计划和任务拆分；
- `research.md`：调研和设计时的依据；
- `quickstart.md`：当时项目的快速检查步骤，里面的路径和命令可能已有变化；
- `verification.md`、`completion-audit.md`：测试和人工复查记录，内容较长；
- `real-platform-acceptance-matrix.md`：哪些行为真实测过，哪些只是单元测试；
- `independent-*.md`：几次独立复核结果。

文档中的状态只对**写作当时**有效。尤其不要把模拟器测试、API 返回成功和真实 Facebook 名单采集全部完成当成同一件事。出现结论不一致时，优先看对应日期的原始测试记录，重新在自己的设备上验证。

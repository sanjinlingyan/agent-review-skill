# 维护

公开仓库只放流程、代码、接口和合成测试。真实标准、逐题映射、交付、日志和报告放在仓库外，默认是仓库旁的 `.agent-review/runs/`。

核心代码使用 Python 3.10+ 标准库。审查 PDF、图片或原生工程时，使用运行环境里已经授权的工具，并在证据的 `origin` 里标明是静态核验、历史复用还是本次实测。

验证：

```sh
python -m unittest discover -s tests -v
python tools/check_public.py --tracked
```

测试在 Windows 和 Linux 上运行。`execute` 的当前实现是已审阅命令的宿主副本，测试覆盖了副本、重复执行和未授权拒绝。它不启动固定容器镜像；需要容器隔离时，应把镜像和网络限制写进新的执行计划，而不是把宿主副本描述成沙箱。

合成材料由 `python scripts/review.py demo --out <仓库外目录>` 生成，内容是虚构的。真实交付上的审查留在私有运行目录，不提交报告。

许可证：MIT。Review Gate 的来源和未纳入的工程检查见 [reuse.md](reuse.md)。

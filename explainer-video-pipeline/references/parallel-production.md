# 同001项目多对话：隔离与共享资源

可以并行筹备、各自生成队列、等待云端任务、构建不同草稿。各片文件隔离与共享资源调度是两件事：不承诺浏览器账户/额度天然隔离，网页并行本轮未做付费实测。

## 每片单写者、不同目录

- 每个对话使用独立episode目录（含题材/期号/创建后缀），独立inputs/assets/generation/review/deliverables。不能共用output、temp、plan.json或“最新下载”文件。
- 当前同一期只能有一个活动写者；续做读取已有状态，不能再次init或同时改同一计划。引用其他片的已审素材必须显式复制到本片并登记来源SHA。
- `pipeline.py init`拒绝覆盖；随后`project_memory.py init --project <本片> --profile generic`生成project_instance_id及run_id，把身份/策略写入计划。计划、shots清单与image_jobs队列继承同一project_identity；绑定器对新身份计划拒绝其他片队列、项目外下载和跨目录输出。队列不能仅凭S001等镜号绑定。
- 状态初始化/checkpoint自动持有本片`.pipeline-locks`锁；原子UUID区分归属，但不能取代“同片单写者”。编译或自制生成脚本若可能撞同片，使用coordination.resource_lock(Path(project), project/'.pipeline-locks')包住整个读改写阶段。
- 项目根AGENTS、基线、频道档案、技能和已接受范例作为只读共享资料。每片使用自己的档案快照和输入；不更新全局current_episode指针来传进度，也不读取另一对话的活动计划。新规则维护集中进行，制作中的规则版本固定在本片状态。

## 浏览器和下载

每片新建并绑定独立Flow项目/GPT对话与标签页，记录provider、browser_id、tab_id、完整project_url、project_instance_id、run_id。每次提交前核对这些字段及身份参考、设置、镜号。不要操作另一片现有Flow项目、切换公共当前页或复用其他对话下载队列。一个任务只绑定本片登记的真实task_id、asset_id、下载路径与SHA，不按Downloads中最新文件猜结果。

同账户额度是共享的，单片5次预算不等于总账户额度无限；排队/等待任务可以并行。提交、参考选择、公共设置和下载操作若共享同一可变浏览器界面，需排队或在持有同一资源锁的调度进程内执行；不能只写owner.json就宣称已加锁。浏览器工具调用没有接入锁的情况下，保守顺序操作共享步骤，保持云端生成等待和本地制作并行。

可被命令行调度的生成脚本使用：`python scripts/coordination.py run --resource "browser:edge:flow:current-account" --lock-root "D:/VideoProjects/.video-coordination/locks" -- <真实命令和参数>`。同账户共享界面使用相同资源键；不同目录的cwd不能使锁根分裂。此CLI只保护它运行的子命令，不能自动锁住其他对话的CUA工具调用。

## 剪映全局索引

`pipeline.py install`已按规范化绝对索引路径，在索引父目录`.pipeline-locks`持有OS进程锁，从首次读索引直到写回/复核。不同片build可以并行，install同一索引会排队；名称、路径、草稿ID分别唯一。原关闭编辑器、索引SHA、备份和保全旧记录检查保留。

不要删除持久.lock文件，OS在进程结束时释放占用；owner.json仅诊断信息，可能过期，不能凭它判锁/抢锁。旧脚本、其他软件和剪映不遵守本锁，仍不能同时改索引；新对话必须使用当前引擎。

测试证据在工具箱`_qa/parallel_isolation_20261010/latest_result.json`。只在假索引与独立测试目录验证同键互斥、不同键并行、超时、进程退出释放及索引保全；不宣称真实网页并发或两部成片同时完成已经实测。

## CUA跨工具调用持锁（当前助手）

目前已提供hold/release，在后台进程持有真实OS锁，跨多次浏览器工具调用有效。所有同账户共享界面的对话使用同一个resource和lock-root（例如browser:edge:flow:current-account和001/.video-coordination/locks）。启动：

`python scripts/coordination.py hold --resource "browser:edge:flow:current-account" --lock-root "D:/VideoProjects/.video-coordination/locks" --owner-id "<本片实例>/<run_id>" --timeout 0 --ttl 600`

保持工具返回的exec session运行。只有见到event:acquired才可操作；保存实际lease_file、lease_id、release_token。获取失败返回75，继续本片其他工作，稍后再申请。提交/参考选择/下载结束，立即执行：

`python scripts/coordination.py release --lease-file "<实际lease_file>" --lease-id "<实际lease_id>" --token "<实际release_token>"`

云端生成等待期间释放锁，让别片提交；下载再申请。TTL到期或helper退出必须重新获取，不沿用过期凭据。release仅释放匹配lease，旧token/文件不能释放新lease；不要删除.lock。该助手需要所有参与对话遵守，同步前旧脚本不受约束；网站内账户行为、服务限流和未接入流程的人仍不受此锁控制。已在假目录测试互斥、释放、TTL与旧token拒绝，未执行付费网页并发实测。

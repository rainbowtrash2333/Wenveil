"""
流水线编排模块。

负责协调文件发现、并发转换、结果合并和输出写入的完整流程。
使用 ThreadPoolExecutor 实现并发文件转换，通过 tqdm 显示进度。
"""

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

from ocr.config import Config
from ocr.converter import DocumentConverter
from ocr.discover import discover_projects
from ocr.merger import merge_results, write_merged_output
from ocr.profiling import ProfileSession, timed
from ocr.safety import safe_id

logger = logging.getLogger("wenveil.ocr")


def _convert_one(converter: DocumentConverter, filepath: Path):
    """测量单个 worker 从进入转换到返回的真实执行时间。"""

    profile_session = getattr(converter, "profile_session", None)
    document_id = safe_id(filepath.name)
    if profile_session is not None:
        profile_session.record_event(
            "pipeline.worker_enter",
            document_id=document_id,
        )
    started = time.perf_counter()
    if profile_session is not None:
        profile_session.record_event(
            "pipeline.worker_convert_before",
            document_id=document_id,
        )
    try:
        result = converter.convert(filepath)
    except Exception:
        if profile_session is not None:
            profile_session.record_event(
                "pipeline.worker_convert_error",
                document_id=document_id,
            )
        raise
    if profile_session is not None:
        profile_session.record_event(
            "pipeline.worker_convert_done",
            document_id=document_id,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    if profile_session is not None:
        profile_session.record_event(
            "pipeline.worker_return",
            document_id=document_id,
        )
    return result, elapsed_ms


def _create_progress_bar(total: int, show: bool):
    """创建 tqdm 进度条（如果启用）。

    Args:
        total: 进度条总步数（文件总数）。
        show: 是否显示进度条。

    Returns:
        tqdm 实例或 None。
    """
    if not show:
        return None
    from tqdm import tqdm
    return tqdm(total=total, desc="转换进度", unit="file")


def run_pipeline(config: Config, resume: bool = False) -> Dict[str, Path]:
    """运行完整的文档转换与合并流水线。

    执行步骤：
    1. 发现根目录下的所有项目及其包含的支持文件
    2. 逐项目进行并发文件转换
    3. 将每个项目的转换结果合并为单个 Markdown 文档
    4. 输出到独立的 OCR 输出目录

    单个文件转换失败不会中断批量处理，错误会被记录到日志。

    Args:
        config: 全局配置对象。
        resume: 断点续跑模式。为 True 时不清空已有输出目录，
            并跳过已存在合并输出的项目（用于长任务中断后继续）。

    Returns:
        一个字典，键为项目名称，值为输出文件的 Path 对象。
        只包含成功输出的项目。
    """
    profile_session = None
    if config.profiling.enabled:
        profile_session = ProfileSession(
            config,
            include_page_profiles=config.profiling.include_page_profiles,
        )
    output_paths: Dict[str, Path] = {}
    pbar = None
    try:
        # ——— 第 1 步：文件发现 ———
        if profile_session is None:
            projects = discover_projects(config.app)
        else:
            with profile_session.stage("pipeline.discover"):
                projects = discover_projects(config.app)
        if not projects:
            logger.warning("没有需要处理的项目")
            return {}

        merged_dir = Path(config.output.directory)

        # 断点续跑：跳过已有合并输出的项目
        if resume and merged_dir.exists():
            if profile_session is None:
                skipped = []
                for name in list(projects.keys()):
                    out = merged_dir / config.output.merged_filename_template.format(
                        project_name=safe_id(name),
                        safe_id=safe_id(name),
                    )
                    if out.exists():
                        skipped.append(name)
                        del projects[name]
            else:
                with profile_session.stage("pipeline.resume_filter"):
                    skipped = []
                    for name in list(projects.keys()):
                        out = merged_dir / config.output.merged_filename_template.format(
                            project_name=safe_id(name),
                            safe_id=safe_id(name),
                        )
                        if out.exists():
                            skipped.append(name)
                            del projects[name]
            if skipped:
                logger.info("断点续跑：跳过已完成项目 %d 个", len(skipped))
            if not projects:
                logger.info("所有项目均已完成，无需处理")
                return {}

        if profile_session is None:
            converter = DocumentConverter(config)
        else:
            with profile_session.stage("pipeline.converter_init"):
                converter = DocumentConverter(config, profile_session=profile_session)
        total_files = sum(len(files) for files in projects.values())

        # 清空独立 OCR 输出目录（断点续跑时保留已有输出）
        if not resume and merged_dir.exists():
            if profile_session is None:
                shutil.rmtree(merged_dir)
            else:
                with profile_session.stage("pipeline.output_cleanup"):
                    shutil.rmtree(merged_dir)
            logger.info("已清空 OCR 输出目录")

        # 创建全局进度条（跨所有项目共享）
        pbar = _create_progress_bar(total_files, config.concurrency.show_progress)

        # ——— 第 2~4 步：转换 + 合并 + 输出（逐项目） ———
        for project_name, file_paths in projects.items():
            # 确定项目根目录（向上查找直到目录名匹配）
            project_root = file_paths[0].parent
            while (
                project_root.name != project_name
                and project_root != Path(config.app.root_dir)
            ):
                project_root = project_root.parent

            logger.info(
                "正在处理项目 id=%s（共 %d 个文件）", safe_id(project_name), len(file_paths)
            )

            project_started = time.perf_counter()
            project_stages: Dict[str, float] = {}

            # 并发转换该项目中的所有文件
            if profile_session is None:
                converted_files = _convert_files(
                    converter, file_paths, config, pbar
                )
            else:
                with timed(project_stages, "files.convert"):
                    converted_files = _convert_files(
                        converter, file_paths, config, pbar
                    )

            # 合并为单个 Markdown 文档
            if profile_session is None:
                merged_content = merge_results(
                    project_name, project_root, converted_files
                )
            else:
                with timed(project_stages, "project.merge"):
                    merged_content = merge_results(
                        project_name, project_root, converted_files
                    )

            # 写入输出文件
            if profile_session is None:
                output_path = write_merged_output(
                    project_name, merged_dir, merged_content, config.output
                )
            else:
                with timed(project_stages, "project.write"):
                    output_path = write_merged_output(
                        project_name, merged_dir, merged_content, config.output
                    )
            output_paths[safe_id(project_name)] = output_path
            if profile_session is not None:
                try:
                    output_size_bytes = output_path.stat().st_size
                except OSError:
                    output_size_bytes = None
                profile_session.record_project(
                    project_id=safe_id(project_name),
                    file_count=len(file_paths),
                    elapsed_ms=(time.perf_counter() - project_started) * 1000,
                    stages_ms=project_stages,
                    status="success",
                    output_size_bytes=output_size_bytes,
                )
    finally:
        # 确保进度条在异常退出时也被正确关闭
        if pbar is not None:
            pbar.close()
        if profile_session is not None and config.profiling.output:
            try:
                profile_session.write(config.profiling.output)
                logger.info("性能 profile 已写入")
            except Exception as error:
                logger.error("性能 profile 写入失败 type=%s", type(error).__name__)

    success = sum(1 for p in output_paths.values() if p.exists())
    logger.info(
        "流水线完成: %d/%d 个项目处理成功",
        success, len(projects),
    )

    return output_paths


def _convert_files(
    converter: DocumentConverter,
    file_paths: List[Path],
    config: Config,
    pbar,
) -> List[Tuple[Path, str]]:
    """使用线程池并发转换一组文件。

    提交所有文件到 ThreadPoolExecutor，通过 as_completed 按完成顺序收集结果。
    单个文件转换失败不影响其他文件，错误信息将作为内容写入结果。

    Args:
        converter: 文档转换器实例。
        file_paths: 待转换的文件路径列表。
        config: 全局配置对象，用于获取并发数。
        pbar: tqdm 进度条实例（可以为 None）。

    Returns:
        (文件路径, Markdown内容) 的列表，按路径排序。
    """
    results: List[Tuple[Path, str]] = []

    profile_session = getattr(converter, "profile_session", None)
    executor = ThreadPoolExecutor(max_workers=config.concurrency.max_workers)
    try:
        # 提交所有转换任务
        future_to_path = {}
        submitted_at = {}
        for fp in file_paths:
            document_id = safe_id(fp.name)
            if profile_session is not None:
                profile_session.record_event(
                    "pipeline.submit_before",
                    document_id=document_id,
                )
            submitted_at[fp] = time.perf_counter()
            future = executor.submit(_convert_one, converter, fp)
            future_to_path[future] = fp
            if profile_session is not None:
                profile_session.record_event(
                    "pipeline.submit_after",
                    document_id=document_id,
                )

        # 按完成顺序收集结果
        if profile_session is not None:
            profile_session.record_event("pipeline.future_wait_begin")
        for future in as_completed(future_to_path):
            fp = future_to_path[future]
            document_id = safe_id(fp.name)
            if profile_session is not None:
                profile_session.record_event(
                    "pipeline.future_done",
                    document_id=document_id,
                )
                profile_session.record_event(
                    "pipeline.future_result_before",
                    document_id=document_id,
                )
            try:
                md_content, worker_elapsed_ms = future.result()
                if profile_session is not None:
                    profile_session.record_event(
                        "pipeline.future_result_after",
                        document_id=document_id,
                    )
                if profile_session is not None:
                    profile_session.add_stage(
                        "pipeline.worker_execution",
                        worker_elapsed_ms,
                    )
                    profile_session.add_stage(
                        "pipeline.future_wait",
                        (time.perf_counter() - submitted_at[fp]) * 1000,
                    )
                if md_content is not None:
                    results.append((fp, md_content))
            except Exception as e:
                logger.error(
                    "转换失败 file_id=%s type=%s",
                    safe_id(fp.name), type(e).__name__,
                )
                results.append((
                    fp,
                    "> *[转换错误，详见安全日志摘要]*\n",
                ))
                if profile_session is not None:
                    profile_session.record_event(
                        "pipeline.future_result_after_error",
                        document_id=document_id,
                    )

            # 更新进度条
            if pbar is not None:
                pbar.update(1)
                pbar.set_postfix_str(f"file-{safe_id(fp.name)}")
        if profile_session is not None:
            profile_session.record_event("pipeline.future_wait_end")
    finally:
        shutdown_started = time.perf_counter()
        if profile_session is not None:
            profile_session.record_event("pipeline.executor_shutdown_before")
        try:
            executor.shutdown(wait=True)
        finally:
            shutdown_elapsed_ms = round(
                (time.perf_counter() - shutdown_started) * 1000,
                3,
            )
            if profile_session is not None:
                profile_session.add_stage(
                    "pipeline.executor_shutdown",
                    shutdown_elapsed_ms,
                )
                profile_session.record_event("pipeline.executor_shutdown_after")

    # 按路径排序确保合并顺序确定
    results.sort(key=lambda x: str(x[0]))
    return results

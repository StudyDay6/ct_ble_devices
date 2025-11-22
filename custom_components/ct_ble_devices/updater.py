"""自动更新模块 - 自动检查并更新集成代码"""
import asyncio
import logging
import aiohttp
import aiofiles
import json
import shutil
from pathlib import Path
from packaging import version
from typing import Optional, Tuple
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import persistent_notification

_LOGGER = logging.getLogger(__name__)

# GitHub 仓库信息
GITHUB_REPO = "StudyDay6/ct_ble_devices"  # 修改为你的仓库
GITHUB_API_BASE = "https://api.github.com/repos"
CHECK_INTERVAL = timedelta(hours=24)  # 每24小时检查一次
AUTO_UPDATE_ENABLED = True  # 是否启用自动更新
RETRY_ON_FAILURE = True  # 更新失败后是否立即重试
MAX_RETRY_ATTEMPTS = 3  # 最大重试次数
RETRY_DELAY = timedelta(minutes=30)  # 重试延迟时间（30分钟）


async def async_setup_auto_update(hass: HomeAssistant, entry: ConfigEntry) -> Optional['IntegrationUpdater']:
    """设置自动更新功能
    
    Args:
        hass: Home Assistant 实例
        entry: 配置条目
        
    Returns:
        IntegrationUpdater 实例，如果启用失败则返回 None
    """
    if not AUTO_UPDATE_ENABLED:
        return None
    
    try:
        # 获取当前版本
        integration_path = Path(__file__).parent
        manifest_path = integration_path / "manifest.json"
        current_version = "1.0.0"
        
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
                current_version = manifest.get("version", "1.0.0")
        else:
            _LOGGER.warning("manifest.json 不存在，使用默认版本: %s", current_version)
        
        # 创建并启动更新器
        updater = IntegrationUpdater(hass, integration_path, current_version, entry.entry_id)
        await updater.start()
        
        _LOGGER.info("自动更新功能已启动（当前版本: %s）", current_version)
        return updater
        
    except Exception as e:
        _LOGGER.error("启动自动更新失败: %s", e, exc_info=True)
        return None


class IntegrationUpdater:
    """集成自动更新器"""
    
    def __init__(self, hass: HomeAssistant, integration_path: Path, current_version: str, entry_id: Optional[str] = None):
        """初始化更新器"""
        self.hass = hass
        self.integration_path = integration_path
        self.current_version = current_version
        self.entry_id = entry_id
        self.last_check: Optional[datetime] = None
        self.update_task: Optional[asyncio.Task] = None
        self.auto_reload = True  # 是否自动重载集成
        self.last_failed_version: Optional[str] = None  # 上次失败的版本
        self.retry_count: int = 0  # 当前重试次数
        
    async def start(self):
        """启动自动更新检查"""
        if not AUTO_UPDATE_ENABLED:
            return
        
        # 延迟启动，避免影响集成初始化
        try:
            await asyncio.sleep(60)  # 等待60秒后开始检查
        except asyncio.CancelledError:
            return
        
        # 立即检查一次（启动时检查）
        await self.check_and_update()  # 忽略返回值，启动时检查失败不影响后续定期检查
        
        # 启动定期检查任务
        self.update_task = self.hass.async_create_background_task(
            self._periodic_check(),
            "ct_ble_devices_auto_update"
        )
    
    async def _periodic_check(self):
        """定期检查更新"""
        while True:
            try:
                now = datetime.now()
                
                # 检查是否需要重试失败的更新
                should_retry = False
                if RETRY_ON_FAILURE and self.last_failed_version:
                    # 检查是否到了重试时间
                    if self.last_check and (now - self.last_check) >= RETRY_DELAY:
                        if self.retry_count < MAX_RETRY_ATTEMPTS:
                            should_retry = True
                            _LOGGER.info("准备重试更新到版本 %s (第 %d/%d 次)", 
                                        self.last_failed_version, self.retry_count + 1, MAX_RETRY_ATTEMPTS)
                        else:
                            # 超过最大重试次数，清除失败记录，等待下一次定期检查
                            _LOGGER.warning("已达到最大重试次数 (%d)，放弃更新版本 %s，等待下一次定期检查", 
                                          MAX_RETRY_ATTEMPTS, self.last_failed_version)
                            self.last_failed_version = None
                            self.retry_count = 0
                
                # 检查是否需要定期检查更新
                should_check = False
                if self.last_check is None or (now - self.last_check) > CHECK_INTERVAL:
                    should_check = True
                
                # 执行检查或重试
                if should_retry or should_check:
                    success, attempted_version = await self.check_and_update()
                    
                    if success:
                        # 更新成功，清除失败记录
                        self.last_failed_version = None
                        self.retry_count = 0
                        self.last_check = now
                    else:
                        # 更新失败
                        if should_retry:
                            # 这是重试，增加重试计数
                            self.retry_count += 1
                            self.last_check = now  # 更新检查时间，用于计算下次重试时间
                            _LOGGER.warning("重试更新失败 (第 %d/%d 次)", self.retry_count, MAX_RETRY_ATTEMPTS)
                        elif should_check and attempted_version:
                            # 这是首次检查失败，记录失败版本
                            self.last_failed_version = attempted_version
                            self.retry_count = 1
                            self.last_check = now
                            if RETRY_ON_FAILURE:
                                _LOGGER.info("更新失败，将在 %d 分钟后重试", RETRY_DELAY.total_seconds() / 60)
                
                # 等待一段时间后再次检查
                await asyncio.sleep(300)  # 每5分钟检查一次是否需要更新
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error("自动更新检查出错: %s", e, exc_info=True)
                await asyncio.sleep(300)  # 出错后等待5分钟再试
    
    async def check_and_update(self) -> Tuple[bool, Optional[str]]:
        """检查并更新集成
        
        Returns:
            Tuple[bool, Optional[str]]: (是否成功, 尝试更新的版本号)
        """
        try:
            latest_version, download_url = await self._get_latest_version()
            
            if not latest_version:
                return False, None
            
            # 比较版本
            try:
                current_ver = version.parse(self.current_version)
                latest_ver = version.parse(latest_version)
                
                if latest_ver <= current_ver:
                    return False, None
                
            except Exception as e:
                _LOGGER.error("版本比较失败: %s", e)
                return False, None
            
            _LOGGER.info("发现新版本: %s (当前: %s)，开始更新...", latest_version, self.current_version)
            
            # 自动下载并更新
            if await self._download_and_update(download_url, latest_version):
                _LOGGER.info("集成已自动更新到版本: %s", latest_version)
                await self._notify_update_success(latest_version, reloaded=True)
                return True, latest_version
            else:
                _LOGGER.error("自动更新失败")
                return False, latest_version
                
        except Exception as e:
            _LOGGER.error("检查更新时出错: %s", e, exc_info=True)
            return False, None
    
    async def _get_latest_version(self) -> Tuple[Optional[str], Optional[str]]:
        """获取最新版本信息"""
        url = f"{GITHUB_API_BASE}/{GITHUB_REPO}/releases/latest"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        latest_version = data.get("tag_name", "").lstrip("v")
                        
                        if not latest_version:
                            _LOGGER.warning("无法从响应中提取版本号")
                            return None, None
                        
                        # 获取下载 URL（ZIP 文件）
                        assets = data.get("assets", [])
                        download_url = None
                        for asset in assets:
                            if asset.get("name", "").endswith(".zip"):
                                download_url = asset.get("browser_download_url")
                                break
                        
                        # 如果没有找到 ZIP，使用源码 ZIP
                        if not download_url:
                            download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/v{latest_version}.zip"
                        
                        return latest_version, download_url
                    elif response.status == 404:
                        _LOGGER.warning("仓库或 Release 不存在 (404)")
                        return None, None
                    else:
                        _LOGGER.warning("获取最新版本失败，状态码: %d", response.status)
                        return None, None
                        
        except asyncio.TimeoutError:
            _LOGGER.error("获取最新版本超时")
            return None, None
        except aiohttp.ClientError as e:
            _LOGGER.error("网络请求失败: %s", e)
            return None, None
        except Exception as e:
            _LOGGER.error("获取最新版本信息失败: %s", e, exc_info=True)
            return None, None
    
    async def _download_and_update(self, download_url: str, new_version: str) -> bool:
        """下载并更新集成"""
        import zipfile
        import tempfile
        
        temp_dir = None
        try:
            # 创建临时目录
            temp_dir = Path(tempfile.mkdtemp())
            zip_path = temp_dir / "update.zip"
            
            # 下载 ZIP 文件
            _LOGGER.info("正在下载新版本...")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_url,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status != 200:
                        _LOGGER.error("下载失败，状态码: %d", response.status)
                        return False
                    
                    async with aiofiles.open(zip_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
            
            # 解压 ZIP 文件
            _LOGGER.info("正在解压新版本...")
            extract_dir = temp_dir / "extract"
            extract_dir.mkdir()
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # 找到集成目录（HACS 标准结构：custom_components/ct_ble_devices/）
            extracted_path = None
            
            # 方式1：查找解压根目录下的 custom_components/ct_ble_devices/ 结构（HACS 标准）
            custom_components_path = extract_dir / "custom_components" / "ct_ble_devices"
            if custom_components_path.exists() and (custom_components_path / "manifest.json").exists():
                extracted_path = custom_components_path
            
            # 方式2：查找版本号前缀目录下的 custom_components/ct_ble_devices/（如 ct_ble_devices-1.0.1/custom_components/ct_ble_devices/）
            if not extracted_path:
                for item in extract_dir.iterdir():
                    if item.is_dir() and "ct_ble_devices" in item.name.lower():
                        versioned_custom_components = item / "custom_components" / "ct_ble_devices"
                        if versioned_custom_components.exists() and (versioned_custom_components / "manifest.json").exists():
                            extracted_path = versioned_custom_components
                            break
            
            # 方式3：查找直接包含 manifest.json 的目录（兼容旧结构）
            if not extracted_path:
                for item in extract_dir.iterdir():
                    if item.is_dir():
                        if (item / "manifest.json").exists():
                            extracted_path = item
                            break
                        sub_integration = item / "ct_ble_devices"
                        if sub_integration.exists() and (sub_integration / "manifest.json").exists():
                            extracted_path = sub_integration
                            break
            
            if not extracted_path:
                _LOGGER.error("无法找到集成目录，请确保 ZIP 包含 custom_components/ct_ble_devices/ 或 ct_ble_devices/")
                return False
            
            # 备份当前版本
            backup_path = self.integration_path.parent / f"{self.integration_path.name}.backup"
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.copytree(self.integration_path, backup_path)
            _LOGGER.info("已创建备份: %s", backup_path)
            
            # 使用临时目录进行原子更新（先完整更新到临时目录，再原子替换）
            staging_path = self.integration_path.parent / f"{self.integration_path.name}.staging"
            if staging_path.exists():
                shutil.rmtree(staging_path)
            
            # 先复制当前版本到临时目录（保留现有文件）
            shutil.copytree(self.integration_path, staging_path)
            
            # 更新文件到临时目录（排除某些文件）
            exclude_files = {'__pycache__', '.git', 'venv', '.backup', '.staging', '*.pyc', '*.pyo'}
            file_count = 0
            required_files = ['manifest.json', '__init__.py']  # 必需文件列表
            found_required_files = set()
            
            for item in extracted_path.rglob('*'):
                if item.is_file():
                    file_count += 1
                    # 检查是否应该排除
                    should_exclude = False
                    for exclude in exclude_files:
                        if exclude in str(item):
                            should_exclude = True
                            break
                    
                    if should_exclude:
                        continue
                    
                    # 计算相对路径
                    rel_path = item.relative_to(extracted_path)
                    target_path = staging_path / rel_path
                    
                    # 记录必需文件
                    if rel_path.name in required_files:
                        found_required_files.add(rel_path.name)
                    
                    # 创建目标目录
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # 复制文件到临时目录
                    shutil.copy2(item, target_path)
            
            # 验证必需文件是否存在
            missing_files = set(required_files) - found_required_files
            if missing_files:
                _LOGGER.error("新版本缺少必需文件: %s", missing_files)
                shutil.rmtree(staging_path)
                return False
            
            # 验证 manifest.json 是否有效
            staging_manifest = staging_path / "manifest.json"
            if staging_manifest.exists():
                try:
                    with open(staging_manifest) as f:
                        manifest = json.load(f)
                        if "version" not in manifest or manifest.get("version") != new_version:
                            _LOGGER.warning("manifest.json 版本号不匹配，将更新为: %s", new_version)
                except Exception as e:
                    _LOGGER.error("验证 manifest.json 失败: %s", e)
                    shutil.rmtree(staging_path)
                    return False
            
            _LOGGER.info("文件更新到临时目录完成: 共处理 %d 个文件", file_count)
            
            # 更新版本信息到临时目录
            await self._update_version_info_in_path(staging_path, new_version)
            
            # 原子替换：先重命名当前目录，再重命名临时目录
            old_path = self.integration_path.parent / f"{self.integration_path.name}.old"
            if old_path.exists():
                shutil.rmtree(old_path)
            
            try:
                # 将当前目录重命名为 .old
                self.integration_path.rename(old_path)
                # 将临时目录重命名为目标目录（原子操作）
                staging_path.rename(self.integration_path)
                _LOGGER.info("原子更新完成（旧版本保存在 .old 目录，重载成功后清理）")
                    
            except Exception as e:
                _LOGGER.error("原子替换失败: %s", e)
                # 尝试恢复
                if old_path.exists() and not self.integration_path.exists():
                    old_path.rename(self.integration_path)
                if staging_path.exists():
                    shutil.rmtree(staging_path)
                raise
            
            # 自动重载集成
            reload_success = False
            if self.auto_reload and self.entry_id:
                await asyncio.sleep(2)  # 等待文件系统同步
                try:
                    reload_success = await self._reload_integration()
                    if reload_success:
                        # 重载成功，清理旧版本目录
                        try:
                            if old_path.exists():
                                shutil.rmtree(old_path)
                                _LOGGER.info("已清理旧版本目录")
                        except Exception as e:
                            _LOGGER.warning("清理旧版本目录失败: %s", e)
                        
                        await self._notify_update_success(new_version, reloaded=True)
                    else:
                        _LOGGER.warning("自动重载失败，但文件已更新")
                        await self._notify_restart_required(new_version)
                except Exception as e:
                    _LOGGER.error("自动重载集成失败: %s", e, exc_info=True)
                    # 重载失败，尝试回滚
                    _LOGGER.warning("由于重载失败，尝试回滚到旧版本...")
                    try:
                        if old_path.exists():
                            if self.integration_path.exists():
                                shutil.rmtree(self.integration_path)
                            old_path.rename(self.integration_path)
                            _LOGGER.info("已回滚到旧版本")
                            await self._notify_update_failed("重载失败，已回滚")
                        else:
                            # 如果没有 .old 目录，尝试从备份恢复
                            backup_path = self.integration_path.parent / f"{self.integration_path.name}.backup"
                            if backup_path.exists():
                                if self.integration_path.exists():
                                    shutil.rmtree(self.integration_path)
                                shutil.copytree(backup_path, self.integration_path)
                                _LOGGER.info("已从备份恢复")
                                await self._notify_update_failed("重载失败，已从备份恢复")
                            else:
                                await self._notify_update_failed("重载失败且无法回滚，请手动检查")
                        return False
                    except Exception as rollback_error:
                        _LOGGER.error("回滚失败: %s", rollback_error, exc_info=True)
                        await self._notify_update_failed("重载失败且回滚失败，请手动检查")
                        return False
            else:
                # 无法自动重载，清理旧版本目录（因为已经确认文件更新成功）
                try:
                    if old_path.exists():
                        shutil.rmtree(old_path)
                except Exception as e:
                    _LOGGER.warning("清理旧版本目录失败: %s", e)
                await self._notify_restart_required(new_version)
            
            return reload_success if self.auto_reload else True
            
        except Exception as e:
            _LOGGER.error("下载和更新过程中出错: %s", e, exc_info=True)
            
            # 尝试恢复备份
            backup_path = self.integration_path.parent / f"{self.integration_path.name}.backup"
            if backup_path.exists():
                _LOGGER.warning("尝试恢复备份...")
                try:
                    if self.integration_path.exists():
                        shutil.rmtree(self.integration_path)
                    shutil.copytree(backup_path, self.integration_path)
                    _LOGGER.info("已恢复备份")
                except Exception as restore_error:
                    _LOGGER.error("恢复备份失败: %s", restore_error)
            
            return False
            
        finally:
            # 清理临时文件
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    _LOGGER.warning("清理临时文件失败: %s", e)
    
    async def _update_version_info(self, new_version: str):
        """更新版本信息"""
        await self._update_version_info_in_path(self.integration_path, new_version)
    
    async def _update_version_info_in_path(self, target_path: Path, new_version: str):
        """更新指定路径的版本信息"""
        try:
            manifest_path = target_path / "manifest.json"
            if manifest_path.exists():
                async with aiofiles.open(manifest_path, 'r') as f:
                    content = await f.read()
                    manifest = json.loads(content)
                    manifest['version'] = new_version
                
                async with aiofiles.open(manifest_path, 'w') as f:
                    await f.write(json.dumps(manifest, indent=2))
                
                _LOGGER.info("版本信息已更新: %s", new_version)
        except Exception as e:
            _LOGGER.warning("更新版本信息失败: %s", e)
    
    async def _notify_update_success(self, new_version: str, reloaded: bool = False):
        """通知更新成功"""
        if reloaded:
            message = f"CT BLE Devices 已自动更新到版本 {new_version}，集成已自动重载。"
        else:
            message = f"CT BLE Devices 已自动更新到版本 {new_version}。\n请重启 Home Assistant 以应用更改。"
        
        persistent_notification.create(
            self.hass,
            message,
            "CT BLE Devices 自动更新",
            f"ct_ble_devices_update_{new_version}"
        )
    
    async def _notify_update_failed(self, reason: str):
        """通知更新失败"""
        message = f"CT BLE Devices 自动更新失败: {reason}\n请检查日志以获取详细信息。"
        
        persistent_notification.create(
            self.hass,
            message,
            "CT BLE Devices 自动更新失败",
            "ct_ble_devices_update_failed"
        )
    
    async def _notify_restart_required(self, new_version: str):
        """通知需要重启"""
        persistent_notification.create(
            self.hass,
            f"CT BLE Devices 已更新到版本 {new_version}。\n"
            "⚠️ 请重启 Home Assistant 以应用更改。",
            "CT BLE Devices 更新完成",
            "ct_ble_devices_restart_required"
        )
    
    async def _reload_integration(self):
        """自动重载集成"""
        if not self.entry_id:
            _LOGGER.warning("无法重载集成：entry_id 未设置")
            return False
        
        try:
            from homeassistant.config_entries import ConfigEntryState
            
            # 获取配置条目
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if not entry:
                _LOGGER.error("无法找到配置条目: %s", self.entry_id)
                return False
            
            # 检查条目状态
            if entry.state == ConfigEntryState.SETUP_IN_PROGRESS:
                # 延迟重载，等待初始化完成
                await asyncio.sleep(10)
                # 重新检查状态
                entry = self.hass.config_entries.async_get_entry(self.entry_id)
                if not entry or entry.state != ConfigEntryState.LOADED:
                    _LOGGER.warning("配置条目未就绪，跳过自动重载")
                    return False
            elif entry.state != ConfigEntryState.LOADED:
                _LOGGER.warning("配置条目未加载，无法重载: %s", entry.state)
                return False
            
            # 重载集成
            result = await self.hass.config_entries.async_reload(self.entry_id)
            
            if result:
                _LOGGER.info("集成重载成功")
                return True
            else:
                _LOGGER.error("集成重载失败")
                return False
                
        except Exception as e:
            _LOGGER.error("重载集成时出错: %s", e, exc_info=True)
            return False
    
    def stop(self):
        """停止自动更新"""
        if self.update_task:
            self.update_task.cancel()


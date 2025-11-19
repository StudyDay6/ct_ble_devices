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
CHECK_INTERVAL = timedelta(hours=6)  # 每6小时检查一次
AUTO_UPDATE_ENABLED = True  # 是否启用自动更新


async def async_setup_auto_update(hass: HomeAssistant, entry: ConfigEntry) -> Optional['IntegrationUpdater']:
    """设置自动更新功能
    
    Args:
        hass: Home Assistant 实例
        entry: 配置条目
        
    Returns:
        IntegrationUpdater 实例，如果启用失败则返回 None
    """
    if not AUTO_UPDATE_ENABLED:
        _LOGGER.debug("自动更新已禁用")
        return None
    
    try:
        # 获取当前版本
        # 在运行时，集成路径是 config/custom_components/ct_ble_devices/
        integration_path = Path(__file__).parent
        manifest_path = integration_path / "manifest.json"
        current_version = "1.0.0"
        
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
                current_version = manifest.get("version", "1.0.0")
        
        # 创建并启动更新器
        updater = IntegrationUpdater(hass, integration_path, current_version, entry.entry_id)
        await updater.start()
        
        _LOGGER.info("自动更新功能已启动")
        return updater
        
    except Exception as e:
        _LOGGER.warning("启动自动更新失败: %s", e, exc_info=True)
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
        
    async def start(self):
        """启动自动更新检查"""
        if not AUTO_UPDATE_ENABLED:
            _LOGGER.debug("自动更新已禁用")
            return
        
        # 延迟启动，避免影响集成初始化
        await asyncio.sleep(60)  # 等待60秒后开始检查
        
        # 启动定期检查任务
        self.update_task = self.hass.async_create_background_task(
            self._periodic_check(),
            "ct_ble_devices_auto_update"
        )
        _LOGGER.info("自动更新检查已启动")
    
    async def _periodic_check(self):
        """定期检查更新"""
        while True:
            try:
                # 检查是否需要检查更新
                if self.last_check is None or \
                   datetime.now() - self.last_check > CHECK_INTERVAL:
                    await self.check_and_update()
                    self.last_check = datetime.now()
                
                # 等待一段时间后再次检查
                await asyncio.sleep(3600)  # 每小时检查一次是否需要更新
                
            except asyncio.CancelledError:
                _LOGGER.debug("自动更新任务被取消")
                break
            except Exception as e:
                _LOGGER.error("自动更新检查出错: %s", e, exc_info=True)
                await asyncio.sleep(3600)  # 出错后等待1小时再试
    
    async def check_and_update(self) -> bool:
        """检查并更新集成"""
        try:
            latest_version, download_url = await self._get_latest_version()
            
            if not latest_version:
                _LOGGER.debug("无法获取最新版本信息")
                return False
            
            # 比较版本
            if version.parse(latest_version) <= version.parse(self.current_version):
                _LOGGER.debug("当前已是最新版本: %s", self.current_version)
                return False
            
            _LOGGER.info(
                "发现新版本: %s (当前版本: %s)，开始自动更新...",
                latest_version,
                self.current_version
            )
            
            # 自动下载并更新
            if await self._download_and_update(download_url, latest_version):
                _LOGGER.info("集成已自动更新到版本: %s", latest_version)
                await self._notify_update_success(latest_version)
                return True
            else:
                _LOGGER.error("自动更新失败")
                return False
                
        except Exception as e:
            _LOGGER.error("检查更新时出错: %s", e, exc_info=True)
            return False
    
    async def _get_latest_version(self) -> Tuple[Optional[str], Optional[str]]:
        """获取最新版本信息"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{GITHUB_API_BASE}/{GITHUB_REPO}/releases/latest"
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        latest_version = data.get("tag_name", "").lstrip("v")
                        
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
                    else:
                        _LOGGER.warning("获取最新版本失败，状态码: %d", response.status)
                        return None, None
                        
        except Exception as e:
            _LOGGER.error("获取最新版本信息失败: %s", e)
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
            
            # 方式1：查找 custom_components/ct_ble_devices/ 结构（HACS 标准）
            custom_components_path = extract_dir / "custom_components" / "ct_ble_devices"
            if custom_components_path.exists() and (custom_components_path / "manifest.json").exists():
                extracted_path = custom_components_path
                _LOGGER.info("找到 HACS 标准结构: custom_components/ct_ble_devices/")
            else:
                # 方式2：查找直接包含 manifest.json 的目录（兼容旧结构）
                for item in extract_dir.iterdir():
                    if item.is_dir():
                        # 检查是否是集成目录（包含 manifest.json）
                        if (item / "manifest.json").exists():
                            extracted_path = item
                            _LOGGER.info("找到集成目录: %s", item.name)
                            break
                        # 检查是否在子目录中（ct_ble_devices-1.0.0/ct_ble_devices/）
                        sub_integration = item / "ct_ble_devices"
                        if sub_integration.exists() and (sub_integration / "manifest.json").exists():
                            extracted_path = sub_integration
                            _LOGGER.info("找到嵌套集成目录: %s", sub_integration)
                            break
            
            if not extracted_path:
                _LOGGER.error("无法找到集成目录，请确保 ZIP 包含 custom_components/ct_ble_devices/ 或 ct_ble_devices/")
                return False
            
            # 备份当前版本
            backup_path = self.integration_path.parent / f"{self.integration_path.name}.backup"
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.copytree(self.integration_path, backup_path)
            _LOGGER.info("已备份当前版本到: %s", backup_path)
            
            # 更新文件（排除某些文件）
            exclude_files = {'__pycache__', '.git', 'venv', '.backup', '*.pyc', '*.pyo'}
            for item in extracted_path.rglob('*'):
                if item.is_file():
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
                    target_path = self.integration_path / rel_path
                    
                    # 创建目标目录
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # 复制文件
                    shutil.copy2(item, target_path)
            
            _LOGGER.info("文件更新完成")
            
            # 更新版本信息
            await self._update_version_info(new_version)
            
            # 自动重载集成
            if self.auto_reload and self.entry_id:
                _LOGGER.info("正在自动重载集成...")
                await asyncio.sleep(2)  # 等待文件系统同步
                try:
                    await self._reload_integration()
                    await self._notify_update_success(new_version, reloaded=True)
                except Exception as e:
                    _LOGGER.error("自动重载集成失败: %s", e, exc_info=True)
                    await self._notify_restart_required(new_version)
            else:
                # 无法自动重载，提示手动重启
                await self._notify_restart_required(new_version)
            
            return True
            
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
        try:
            manifest_path = self.integration_path / "manifest.json"
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
            if entry.state != ConfigEntryState.LOADED:
                _LOGGER.warning("配置条目未加载，无法重载: %s", entry.state)
                return False
            
            # 重载集成
            _LOGGER.info("开始重载集成: %s", self.entry_id)
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


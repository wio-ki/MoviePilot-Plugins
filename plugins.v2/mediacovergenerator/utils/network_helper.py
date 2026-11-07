"""
网络请求优化工具类
用于优化字体下载和其他网络操作，防止阻塞和超时
"""
import asyncio
import aiohttp
import requests
import os
import shutil
import time
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
import hashlib
import subprocess
from app.log import logger
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class NetworkHelper:
    """网络请求助手类，提供超时控制和重试机制"""

    def __init__(self, timeout: int = 30, max_retries: int = 3, connect_timeout: int = 15):
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_retries = max_retries
        self.session = None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        connector = aiohttp.TCPConnector(
            limit=10,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'MoviePilot-MediaCoverGenerator/1.0'},
            trust_env=True
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self.session:
            await self.session.close()

    async def download_file_async(self, url: str, save_path: Path,
                                 expected_size: Optional[int] = None) -> bool:
        """
        异步下载文件

        Args:
            url: 下载链接
            save_path: 保存路径
            expected_size: 期望的文件大小（字节）

        Returns:
            bool: 下载是否成功
        """
        if not self.session:
            raise RuntimeError("NetworkHelper must be used as async context manager")

        for attempt in range(self.max_retries):
            try:
                logger.info(f"开始下载文件 (尝试 {attempt + 1}/{self.max_retries}): {url}")

                async with self.session.get(url) as response:
                    if response.status == 200:
                        content = await response.read()

                        # 验证文件大小
                        if expected_size and len(content) != expected_size:
                            logger.warning(f"文件大小不匹配: 期望 {expected_size}, 实际 {len(content)}")
                            if attempt < self.max_retries - 1:
                                continue

                        # 确保目录存在
                        save_path.parent.mkdir(parents=True, exist_ok=True)

                        # 写入文件
                        with open(save_path, 'wb') as f:
                            f.write(content)

                        logger.info(f"文件下载成功: {save_path}")
                        return True
                    else:
                        logger.warning(f"下载失败，HTTP状态码: {response.status}")

            except asyncio.TimeoutError:
                logger.warning(f"下载超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            except Exception as e:
                logger.warning(f"下载出错 (尝试 {attempt + 1}/{self.max_retries}): {e}")

            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # 指数退避

        logger.error(f"文件下载失败，已重试 {self.max_retries} 次: {url}")
        return False

    def download_file_sync(self, url: str, save_path: Path,
                           expected_size: Optional[int] = None,
                           proxies: Optional[Dict[str, str]] = None) -> bool:
        """
        同步下载文件：分块写入磁盘，连接超时和读取超时分开算。

        timeout 是两次收到数据之间最多等多久，不是整个文件的时限，
        慢速链路只要一直有数据进来就能下完。下载的字节数和
        Content-Length（或 expected_size）对不上时视为失败并删掉文件。

        Args:
            url: 下载链接
            save_path: 保存路径
            expected_size: 期望的文件大小（字节）
            proxies: requests 格式的代理，例如 settings.PROXY

        Returns:
            bool: 下载是否成功
        """
        timeouts = (min(self.connect_timeout, self.timeout), self.timeout)
        headers = {'User-Agent': 'MoviePilot-MediaCoverGenerator/1.0'}
        for attempt in range(self.max_retries):
            try:
                logger.info(f"开始下载文件 (尝试 {attempt + 1}/{self.max_retries}): {url}")
                started = time.monotonic()
                try:
                    response = requests.get(url, timeout=timeouts, headers=headers,
                                            stream=True, proxies=proxies)
                except requests.exceptions.SSLError:
                    logger.warning(f"SSL验证失败，尝试忽略证书验证 (尝试 {attempt + 1}/{self.max_retries})")
                    response = requests.get(url, timeout=timeouts, headers=headers,
                                            stream=True, proxies=proxies, verify=False)

                with response:
                    if response.status_code != 200:
                        logger.warning(f"下载失败，HTTP状态码: {response.status_code}")
                    elif self.__stream_to_file(response, save_path, expected_size):
                        size = save_path.stat().st_size
                        logger.info(f"文件下载成功: {save_path}（{size} 字节，"
                                    f"用时 {time.monotonic() - started:.1f} 秒）")
                        return True

            except requests.exceptions.Timeout:
                logger.warning(f"下载超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            except Exception as e:
                logger.warning(f"下载出错 (尝试 {attempt + 1}/{self.max_retries}): {e}")
            self.__remove(save_path)

            if attempt < self.max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避

        if self.__wget(url, save_path, expected_size, proxies):
            return True
        logger.error(f"文件下载失败，已重试 {self.max_retries} 次 + wget: {url}")
        return False

    @staticmethod
    def __stream_to_file(response, save_path: Path, expected_size: Optional[int]) -> bool:
        """把响应分块写进文件，并检查有没有下全。"""
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        # Content-Length 是线上传输的字节数（可能被 gzip 过），所以和 raw.tell() 比
        declared = response.headers.get('Content-Length')
        if declared and declared.isdigit():
            received = response.raw.tell()
            if received != int(declared):
                logger.warning(f"文件不完整: 应为 {declared} 字节，只收到 {received} 字节")
                return False
        size = save_path.stat().st_size
        if expected_size and size != expected_size:
            logger.warning(f"文件大小不匹配: 期望 {expected_size}, 实际 {size}")
            return False
        if size == 0:
            logger.warning("下载到的文件是空的")
            return False
        return True

    def __wget(self, url: str, save_path: Path, expected_size: Optional[int],
               proxies: Optional[Dict[str, str]]) -> bool:
        """Python 下载失败时用系统 wget 再试一次，走同一个代理。"""
        if not shutil.which("wget"):
            return False
        logger.info(f"Python下载失败，尝试使用系统 wget 命令: {url}")
        env = os.environ.copy()
        for scheme, proxy in (proxies or {}).items():
            if proxy:
                env[f"{scheme}_proxy"] = proxy
                env[f"{scheme.upper()}_PROXY"] = proxy
        cmd = ["wget", "-q", "-O", str(save_path), f"--connect-timeout={self.connect_timeout}",
               f"--read-timeout={self.timeout}", "--tries=1", url]
        if "github.com" in url or "raw.githubusercontent.com" in url:
            # GitHub 下载可能需要关闭证书验证
            cmd.append("--no-check-certificate")
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            # wget 有读取超时，这里只防它卡死；慢速链路上大字体要好几分钟
            subprocess.run(cmd, check=True, timeout=max(self.timeout * 10, 600), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            size = save_path.stat().st_size if save_path.exists() else 0
            if size > 0 and (not expected_size or size == expected_size):
                logger.info(f"wget 下载成功: {save_path}")
                return True
            logger.warning(f"wget 下载的文件不完整: {size} 字节")
        except Exception as e:
            logger.error(f"wget 下载也失败: {e}")
        self.__remove(save_path)
        return False

    @staticmethod
    def __remove(path: Path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def validate_font_file(font_path: Path) -> bool:
    """
    验证字体文件是否有效

    Args:
        font_path: 字体文件路径

    Returns:
        bool: 字体文件是否有效
    """
    try:
        if not font_path.exists() or font_path.stat().st_size == 0:
            return False

        # 尝试加载字体文件
        from PIL import ImageFont
        font = ImageFont.truetype(str(font_path), 12)
        return True
    except Exception as e:
        logger.warning(f"字体文件验证失败: {font_path}, 错误: {e}")
        return False


def get_file_hash(file_path: Path) -> Optional[str]:
    """
    计算文件的MD5哈希值

    Args:
        file_path: 文件路径

    Returns:
        str: 文件的MD5哈希值，如果文件不存在则返回None
    """
    try:
        if not file_path.exists():
            return None

        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.warning(f"计算文件哈希失败: {file_path}, 错误: {e}")
        return None
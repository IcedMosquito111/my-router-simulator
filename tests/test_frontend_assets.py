# -*- coding: utf-8 -*-
"""
前端静态资源测试。

前端的 IPv6 支持完全依赖这几个约定，一旦被误删就会"静默退化为只能发 IPv4"：
    1. 拓扑快照中的节点必须带 loopback_ipv4 / loopback_ipv6（后端 test_topology_state.py 保证）；
    2. 页面上必须存在协议选择器与目的地址输入框；
    3. 发送分组时必须从节点数据解析地址，而不是继续拼 10.255.x.y 这类字符串。
另外，如果本机存在 node，则顺带做一次内联脚本的语法检查。
"""

import pathlib
import re
import shutil
import subprocess

import pytest

FRONTEND_DIR = pathlib.Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert INDEX_HTML.is_file(), f"前端页面不存在: {INDEX_HTML}"
    return INDEX_HTML.read_text(encoding="utf-8-sig")


def test_protocol_selector_and_address_input_exist(html):
    assert 'id="protocol-select"' in html
    assert '<option value="IPv4">' in html
    assert '<option value="IPv6">' in html
    assert 'id="dst-override"' in html


def test_destination_address_is_resolved_from_node_data(html):
    """回归：早期版本用 parseInt(nodeId) 硬拼 10.255.x.y，导致不可能支持 IPv6"""
    assert "function resolveDestinationAddress" in html
    assert "loopbackIpv6" in html
    assert "loopbackIpv4" in html
    assert "10.255.${" not in html, "不应再手工拼接 IPv4 回环地址"


def test_send_packet_passes_protocol(html):
    send_block = html[html.index("function sendPacket()"):]
    send_block = send_block[:send_block.index("\n        }\n") + 12]
    assert "protocol" in send_block
    assert "sendCommand('send_packet'" in send_block
    # 手输 IPv6 地址时自动切换协议族，避免被后端以"版本不一致"拒绝
    assert "includes(':')" in send_block
    assert "setProtocol('IPv6')" in send_block


def test_snapshot_loopback_fields_are_consumed(html):
    """节点数据必须把回环地址存进 vis 数据集，否则 describeNode / 解析都会拿不到地址"""
    assert "loopbackIpv4: node.loopback_ipv4" in html
    assert "loopbackIpv6: node.loopback_ipv6" in html


def test_routing_table_panel_reports_both_families(html):
    assert "IPv4 ${ipv4Count} 条 / IPv6 ${ipv6Count} 条" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node，跳过 JS 语法检查")
def test_inline_script_syntax(html):
    blocks = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S)
    assert blocks, "未找到内联脚本"
    script = max(blocks, key=len)

    result = subprocess.run(
        ["node", "--check", "-"],
        input=script.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, f"内联 JS 语法错误:\n{result.stderr.decode('utf-8', 'replace')}"

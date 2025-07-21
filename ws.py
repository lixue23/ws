import pandas as pd
import streamlit as st
from io import BytesIO
import base64
import os
import sys
from datetime import datetime
import json
import time
import hashlib
import asyncio
import aiohttp
from aiohttp import ClientTimeout
import socket

# === 必须作为第一个Streamlit命令 ===
st.set_page_config(page_title="清洗服务记录转换工具", page_icon="🧹", layout="wide")

# === 检查是否在 Streamlit 环境中运行 ===
if not hasattr(st, 'session_state'):
    st.error("请使用 'streamlit run [脚本名称].py' 命令运行此应用")
    st.stop()

# === 安全获取DeepSeek API密钥 ===
deepseek_api_key = 'sk-520a254025904231a3fafcd668347b43'

# 1. 首先尝试从环境变量获取
if 'DEEPSEEK_API_KEY' in os.environ:
    deepseek_api_key = os.environ['DEEPSEEK_API_KEY']

# 2. 尝试从st.secrets获取
try:
    if 'DEEPSEEK_API_KEY' in st.secrets:
        deepseek_api_key = st.secrets['DEEPSEEK_API_KEY']
except Exception:
    pass  # 忽略错误

# 3. 如果以上都失败，尝试从.env文件加载
if not deepseek_api_key and os.path.exists('.env'):
    try:
        from dotenv import load_dotenv

        load_dotenv()
        deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')
    except ImportError:
        pass
    except Exception:
        pass

# 检查关键依赖
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode
except ImportError:
    st.error("缺少关键依赖: streamlit-aggrid! 请确保requirements.txt中包含该包")
    st.stop()

# === 主应用代码 ===
st.title("🧹 清洗服务记录转换工具")
st.markdown("""
将无序繁杂的清洗服务记录文本转换为结构化的表格数据，并导出为Excel文件。
""")

# 初始化session state
if 'df' not in st.session_state:
    st.session_state.df = pd.DataFrame()
if 'input_text' not in st.session_state:
    st.session_state.input_text = ""
if 'last_processed' not in st.session_state:
    st.session_state.last_processed = ""
if 'auto_save_counter' not in st.session_state:
    st.session_state.auto_save_counter = 0
if 'api_endpoint' not in st.session_state:
    st.session_state.api_endpoint = "https://api.deepseek.com"
if 'auto_process' not in st.session_state:
    st.session_state.auto_process = False
if 'cache_dict' not in st.session_state:
    st.session_state.cache_dict = {}
if 'batch_size' not in st.session_state:
    st.session_state.batch_size = 10  # 默认批量大小
if 'active_endpoints' not in st.session_state:
    st.session_state.active_endpoints = []

# 在侧边栏显示API密钥状态
with st.sidebar:
    st.subheader("API密钥状态")

    # 显示系统时间
    st.caption(f"系统时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 添加手动输入API密钥的选项
    manual_key = st.text_input("手动输入API密钥", type="password", key="manual_api_key")
    if manual_key:
        deepseek_api_key = manual_key

    if deepseek_api_key:
        masked_key = f"{deepseek_api_key[:8]}...{deepseek_api_key[-4:]}" if len(deepseek_api_key) > 12 else "****"
        st.info(f"当前密钥: {masked_key}")

        # 检查密钥格式
        if not deepseek_api_key.startswith("sk-") or len(deepseek_api_key) < 40:
            st.error("⚠️ API密钥格式无效")
            st.info("密钥应以'sk-'开头，长度至少40字符")
        elif " " in deepseek_api_key:
            st.warning("密钥包含空格，已自动清理")
            deepseek_api_key = deepseek_api_key.strip()

        if st.button("重新加载密钥"):
            st.rerun()
    else:
        st.error("API密钥未配置!")
        st.info("请设置环境变量 DEEPSEEK_API_KEY 或手动输入密钥")
        st.markdown("""
        **本地配置方法:**
        1. 创建 `.env` 文件并添加:
           ```
           DEEPSEEK_API_KEY=sk-your-api-key
           ```
        2. 或在运行前设置环境变量:
           ```bash
           export DEEPSEEK_API_KEY=sk-your-api-key
           streamlit run data.py
           ```
        """)

    # 添加API端点选择
    st.subheader("API端点设置")
    endpoint_options = {
        "官方主端点 (推荐)": "https://api.deepseek.com",
        "备用端点1": "https://api.deepseek.com/v1",
        "备用端点2": "https://api.deepseek.cc"
    }
    selected_endpoint = st.selectbox(
        "选择API端点:",
        list(endpoint_options.keys()),
        index=0
    )
    st.session_state.api_endpoint = endpoint_options[selected_endpoint]
    st.info(f"当前端点: {st.session_state.api_endpoint}")

    # 添加自动处理开关
    st.session_state.auto_process = st.checkbox("自动处理模式", value=st.session_state.auto_process)
    if st.session_state.auto_process:
        st.info("开启后，输入文本变化将自动触发转换")

    # 批量处理设置
    st.subheader("批量处理设置")
    st.session_state.batch_size = st.slider(
        "每批处理记录数",
        min_value=1,
        max_value=50,
        value=st.session_state.batch_size,
        help="增加批量大小可减少API调用次数，提高处理速度"
    )

    # 缓存管理
    st.subheader("缓存管理")
    if st.button("🧹 清除API缓存", help="清除缓存的API响应结果"):
        st.session_state.cache_dict = {}
        st.success("缓存已清除！")

    st.info(f"当前缓存数量: {len(st.session_state.cache_dict)}")

    # 性能统计
    if 'api_response_time' in st.session_state:
        st.subheader("性能统计")
        st.info(f"平均API响应时间: {st.session_state.api_response_time:.2f}秒")
        st.info(f"总API调用次数: {st.session_state.api_call_count}")

# 自动保存状态显示
if st.session_state.auto_save_counter > 0:
    save_time = datetime.now().strftime("%H:%M:%S")
    st.sidebar.success(f"⏱️ 自动保存于: {save_time} (已保存{st.session_state.auto_save_counter}次)")

# 示例文本
sample_text = """
张雨浪 凡尔赛 下午 融创 凡尔赛领馆四期 16栋27-7 15223355185 空调内外机清洗 有异味，可能要全拆洗180，外机在室外150，内机高温蒸汽洗58  未支付 这个要翻外墙，什么时候来

李雪霜 华宇 寸滩派出所楼上 2栋9-8 13983014034 挂机加氟+1空调清洗 加氟一共299 清洗50 未支付

王师傅 龙湖源著 8栋12-3 13800138000 空调维修 不制冷 加氟200 已支付 需要周末上门

刘工 恒大御景半岛 3栋2单元501 13512345678 中央空调深度清洗 全拆洗380 已支付 业主周日下午在家
"""

# 文本输入区域
with st.expander("📝 输入清洗服务记录文本", expanded=True):
    input_text = st.text_area("请输入清洗服务记录（每行一条记录）:",
                              value=st.session_state.input_text or sample_text,
                              height=300,
                              placeholder="请输入清洗服务记录文本...",
                              key="input_text_area")

    # 添加示例下载按钮
    st.download_button("📥 下载示例文本",
                       sample_text,
                       file_name="清洗服务记录示例.txt")

    # 添加保存文本按钮
    if st.button("💾 保存当前文本", key="save_text_button"):
        st.session_state.input_text = input_text
        st.success("文本已保存！")

columns = ['师傅', '项目', '地址', '房号', '客户姓名', '电话号码', '服务内容', '费用', '支付状态', '备注']


# 计算文本哈希值（用于缓存）
def calculate_text_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()


# 检查端点连通性
def is_endpoint_reachable(endpoint):
    try:
        # 提取主机名
        host = endpoint.split("//")[-1].split("/")[0]
        # 检查DNS解析
        socket.getaddrinfo(host, 443)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return False


# 异步API调用函数
async def async_api_request(session, endpoint, payload, timeout=20):
    headers = {
        "Authorization": f"Bearer {deepseek_api_key}",
        "Content-Type": "application/json"
    }

    try:
        start_time = time.time()
        async with session.post(
                f"{endpoint}/chat/completions",
                json=payload,
                headers=headers,
                timeout=ClientTimeout(total=timeout)
        ) as response:
            if response.status == 200:
                response_data = await response.json()
                elapsed = time.time() - start_time

                # 更新性能统计
                if 'api_response_time' not in st.session_state:
                    st.session_state.api_response_time = elapsed
                    st.session_state.api_call_count = 1
                else:
                    total_time = st.session_state.api_response_time * st.session_state.api_call_count
                    st.session_state.api_call_count += 1
                    st.session_state.api_response_time = (total_time + elapsed) / st.session_state.api_call_count

                return response_data
            else:
                error_text = await response.text()
                st.error(f"API错误: {response.status} - {error_text}")
                return None
    except asyncio.TimeoutError:
        st.error(f"API请求超时 ({timeout}秒)")
        return None
    except Exception as e:
        st.error(f"请求异常: {str(e)}")
        return None


# 批量处理记录
async def process_batch(batch_text, endpoint):
    # 检查缓存
    text_hash = calculate_text_hash(batch_text)
    if text_hash in st.session_state.cache_dict:
        return st.session_state.cache_dict[text_hash]

    # 准备API请求
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": """
                你是一个文本解析专家，负责将无序的清洗服务记录文本转换为结构化的表格数据。请根据以下规则处理输入文本，并输出清晰的JSON格式。

                ### 解析规则:
                1. 自动识别11位电话号码
                2. 识别"未支付"和"已支付"状态
                3. 提取费用信息（如180元）
                4. 识别房号格式（如16栋27-7）
                5. 开头的中文名字作为师傅姓名
                6. 剩余内容分割为项目和服务内容

                ### 输出格式:
                请将解析结果输出为JSON格式，包含以下字段:
                - 师傅: 师傅姓名
                - 项目: 项目名称
                - 地址: 地址
                - 房号: 房号
                - 客户姓名: 客户姓名
                - 电话号码: 电话号码
                - 服务内容: 服务内容
                - 费用: 费用
                - 支付状态: 支付状态
                - 备注: 备注

                ### 支持的文本格式示例:
                张雨浪 凡尔赛 下午 融创 凡尔赛领馆四期 16栋27-7 15223355185 空调内外机清洗 有异味，可能要全拆洗180，外机在室外150，内机高温蒸汽洗58 未支付 这个要翻外墙，什么时候来
                李雪霜 华宇 寸滩派出所楼上 2栋9-8 13983014034 挂机加氟+1空调清洗 加氟一共299 清洗50 未支付
                王师傅 龙湖源著 8栋12-3 13800138000 空调维修 不制冷 加氟200 已支付 需要周末上门

                ## 注意事项:
                - 请确保输出的JSON格式正确，方便后续处理。
                - 如果无法解析某条记录，请返回空对象或空列表，并在备注中说明原因。
                - 返回的格式必须严格遵循上述示例格式的字符串，不要携带任何额外的文本或说明，包括```json```。
                - 如果没有指定属性的值，请将该值设置为空字符串。
                - 返回的结果要确保能直接被python的eval函数解析为列表或字典格式。
            """},
            {"role": "user", "content": "请解析以下清洗服务记录文本并输出为JSON格式:\n" + batch_text},
        ],
        "temperature": 0.3,  # 降低随机性，提高一致性
        "max_tokens": 2000,  # 限制输出长度
        "stream": False
    }

    # 创建异步会话
    async with aiohttp.ClientSession() as session:
        response = await async_api_request(session, endpoint, payload,timeout=120)

    if not response or 'choices' not in response or not response['choices']:
        return None

    content = response['choices'][0]['message']['content']

    # 尝试去除JSON标记
    if content.startswith("```json"):
        content = content[7:-3].strip()

    try:
        parsed_data = json.loads(content)
        # 存入缓存
        st.session_state.cache_dict[text_hash] = parsed_data
        return parsed_data
    except Exception as e:
        st.error(f"解析JSON失败: {str(e)}")
        st.code(content, language='json')
        return None


# 获取可用端点列表
def get_available_endpoints():
    endpoints = {
        "官方主端点": "https://api.deepseek.com",
        "备用端点1": "https://api.deepseek.com/v1",
        "备用端点2": "https://api.deepseek.cc"
    }

    available = []
    for name, url in endpoints.items():
        if is_endpoint_reachable(url):
            available.append((name, url))

    # 如果所有端点都不可用，尝试直接IP连接
    if not available:
        st.warning("所有API端点均不可达，尝试使用直接IP连接...")
        try:
            # 尝试解析api.deepseek.com的IP
            ip_list = socket.getaddrinfo("api.deepseek.com", 443)
            if ip_list:
                ip = ip_list[0][4][0]
                available.append(("直接IP连接", f"https://{ip}"))
        except Exception:
            pass

    return available


# 主处理函数
async def process_records():
    # 保存当前文本
    st.session_state.input_text = input_text

    # 检查API密钥
    if not deepseek_api_key:
        st.error("缺少DeepSeek API密钥！请按照侧边栏说明配置")
        return False

    # 获取可用端点
    available_endpoints = get_available_endpoints()
    if not available_endpoints:
        st.error("无法连接到任何DeepSeek API端点，请检查网络连接！")
        return False

    # 创建进度条
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text(f"使用端点: {available_endpoints[0][0]} ({available_endpoints[0][1]})")

    # 初始化性能统计
    if 'api_call_count' not in st.session_state:
        st.session_state.api_call_count = 0
    if 'api_response_time' not in st.session_state:
        st.session_state.api_response_time = 0

    # 限制最大记录数
    max_records = 100
    lines = [line.strip() for line in input_text.strip().split('\n') if line.strip()]
    line_count = len(lines)

    if line_count > max_records:
        st.warning(f"一次最多处理{max_records}条记录（当前{line_count}条），请分批处理")
        return False

    # 分批处理
    batch_size = st.session_state.batch_size
    num_batches = (line_count + batch_size - 1) // batch_size
    all_data = []
    errors = []

    # 创建任务列表
    tasks = []
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, line_count)
        batch_lines = lines[start_idx:end_idx]
        batch_text = "\n".join(batch_lines)

        # 为每个批次使用第一个可用端点
        endpoint = available_endpoints[0][1]
        tasks.append(process_batch(batch_text, endpoint))

    # 执行所有任务
    results = await asyncio.gather(*tasks)

    # 处理结果
    for i, result in enumerate(results):
        progress = int((i + 1) * 100 / num_batches)
        progress_bar.progress(progress)
        status_text.text(f"处理批次 {i + 1}/{num_batches} ({progress}%)")

        if result is None:
            errors.append(f"批次 {i + 1} 处理失败")
            continue

        if isinstance(result, list):
            for record in result:
                if isinstance(record, dict):
                    all_data.append([
                        record.get('师傅', ''),
                        record.get('项目', ''),
                        record.get('地址', ''),
                        record.get('房号', ''),
                        record.get('客户姓名', ''),
                        record.get('电话号码', ''),
                        record.get('服务内容', ''),
                        record.get('费用', ''),
                        record.get('支付状态', ''),
                        record.get('备注', '')
                    ])
                else:
                    errors.append(f"第 {len(all_data) + 1} 条记录格式错误: {record}")
        else:
            errors.append(f"批次 {i + 1} 返回结果不是列表格式") # 无论如何返回数据

    progress_bar.progress(100)
    time.sleep(0.5)
    progress_bar.empty()
    status_text.empty()

    if all_data:
        st.session_state.df = pd.DataFrame(all_data, columns=columns)
        st.session_state.last_processed = input_text

        # 自动缓存数据
        st.session_state.cached_df = st.session_state.df.copy()
        st.session_state.auto_save_counter += 1

        success_msg = f"成功解析 {len(all_data)} 条记录！"
        if num_batches > 1:
            success_msg += f" (分{num_batches}批处理)"
        st.success(success_msg)

        # 显示性能统计
        if st.session_state.api_call_count > 0:
            st.info(f"API调用次数: {st.session_state.api_call_count}次")
            st.info(f"平均响应时间: {st.session_state.api_response_time:.2f}秒")
            st.info(f"总处理时间: {st.session_state.api_response_time * st.session_state.api_call_count:.2f}秒")

        return True
    else:
        st.error("未能解析出任何记录，请检查输入格式！")
        if errors:
            st.warning(f"共发现 {len(errors)} 条错误")
            for error in errors:
                st.error(error)
        return False


# 处理按钮
col1, col2 = st.columns([1, 2])
with col1:
    if st.button("🚀 转换文本为表格", use_container_width=True, key="convert_button") or \
            (st.session_state.auto_process and st.session_state.input_text != st.session_state.last_processed):
        # 使用异步执行
        asyncio.run(process_records())

with col2:
    if st.button("🔄 从缓存恢复数据", use_container_width=True, key="restore_button"):
        if 'cached_df' in st.session_state:
            st.session_state.df = st.session_state.cached_df
            st.success("已从缓存恢复数据！")
        else:
            st.warning("没有找到缓存数据")

# 自动保存计时器
if 'df' in st.session_state and isinstance(st.session_state.df, pd.DataFrame) and not st.session_state.df.empty:
    if st.session_state.auto_save_counter % 5 == 0:  # 每5次操作自动保存
        st.session_state.cached_df = st.session_state.df.copy()
        st.session_state.auto_save_counter += 1

# 只要 session_state['df'] 存在就显示可编辑表格
if 'df' in st.session_state and isinstance(st.session_state.df, pd.DataFrame) and not st.session_state.df.empty:
    st.subheader("清洗服务记录表格（可编辑）")

    # 添加手动保存按钮
    if st.button("💾 手动保存当前表格", key="save_table_button"):
        st.session_state.cached_df = st.session_state.df.copy()
        st.session_state.auto_save_counter += 1
        st.success("表格已保存！")

    gb = GridOptionsBuilder.from_dataframe(st.session_state.df)
    gb.configure_default_column(editable=True, min_column_width=100)
    gb.configure_grid_options(domLayout='normal', enableRangeSelection=True)
    grid_options = gb.build()

    grid_response = AgGrid(
        st.session_state.df,
        gridOptions=grid_options,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        update_mode=GridUpdateMode.MODEL_CHANGED,
        fit_columns_on_grid_load=True,
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        use_container_width=True,
        height=500,
        theme='streamlit'
    )
    # 调试输出，查看响应内容
    print(response.choices[0].message.content)  # 调试输出，查看原始响应内容
    # 保存编辑后的 DataFrame
    st.session_state.df = grid_response['data']

    # 添加统计信息
    col1, col2, col3 = st.columns(3)
    col1.metric("总记录数", len(st.session_state.df))
    payment_counts = st.session_state.df['支付状态'].value_counts()
    if not payment_counts.empty:
        col2.metric("未支付数量", payment_counts.get('未支付', 0))
        col3.metric("已支付数量", payment_counts.get('已支付', 0))
    else:
        col2.metric("未支付数量", 0)
        col3.metric("已支付数量", 0)

    # 导出Excel功能
    st.subheader("导出数据")
    output = BytesIO()

    try:
        # 首选使用xlsxwriter引擎
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state.df.to_excel(writer, index=False, sheet_name='清洗服务记录')
            workbook = writer.book
            worksheet = writer.sheets['清洗服务记录']

            # 设置列宽
            for idx, col in enumerate(st.session_state.df.columns):
                max_len = max(st.session_state.df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(idx, idx, max_len)

            # 设置条件格式
            format_red = workbook.add_format({'bg_color': '#FFC7CE'})
            format_green = workbook.add_format({'bg_color': '#C6EFCE'})

            # 支付状态在第8列（索引7）
            worksheet.conditional_format(1, 7, len(st.session_state.df), 7, {
                'type': 'text',
                'criteria': 'containing',
                'value': '未支付',
                'format': format_red
            })
            worksheet.conditional_format(1, 7, len(st.session_state.df), 7, {
                'type': 'text',
                'criteria': 'containing',
                'value': '已支付',
                'format': format_green
            })

            # 冻结首行和添加筛选器
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, len(st.session_state.df), len(st.session_state.df.columns) - 1)

    except ImportError:
        # xlsxwriter不可用，尝试openpyxl
        try:
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                st.session_state.df.to_excel(writer, index=False, sheet_name='清洗服务记录')
        except ImportError:
            # 两个引擎都不可用，使用默认引擎
            with pd.ExcelWriter(output) as writer:
                st.session_state.df.to_excel(writer, index=False, sheet_name='清洗服务记录')
            st.warning("Excel高级功能不可用，使用基础导出")
    except Exception as e:
        # 其他错误处理
        st.error(f"Excel导出错误: {str(e)}")
        with pd.ExcelWriter(output) as writer:
            st.session_state.df.to_excel(writer, index=False, sheet_name='清洗服务记录')
        st.warning("使用基础Excel导出")

    # 生成下载链接
    excel_data = output.getvalue()
    b64 = base64.b64encode(excel_data).decode()
    href = f'<a href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64}" download="清洗服务记录_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx">⬇️ 下载Excel文件</a>'
    st.markdown(href, unsafe_allow_html=True)

    # 添加数据备份下载
    st.download_button(
        label="📥 下载数据备份 (JSON)",
        data=st.session_state.df.to_json(orient='records', force_ascii=False),
        file_name=f"清洗服务记录备份_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
        mime="application/json"
    )

# 使用说明
st.divider()
st.subheader("使用说明")
st.markdown("""
### 解决API连接问题的方法:

1. **检查网络连接**:
   - 确保您的设备已连接到互联网
   - 尝试访问 https://api.deepseek.com 验证是否可达

2. **更换API端点**:
   - 在侧边栏尝试不同的API端点
   - 推荐使用"官方主端点 (推荐)"

3. **检查防火墙设置**:
   - 确保防火墙没有阻止对DeepSeek API的访问
   - 可能需要允许443端口(HTTPS)的出站连接

4. **DNS问题排查**:
   - 尝试刷新DNS缓存 (命令: `ipconfig /flushdns`)
   - 或使用公共DNS如8.8.8.8 (Google) 或 1.1.1.1 (Cloudflare)

5. **使用代理/VPN**:
   - 如果您的网络限制访问DeepSeek API，尝试使用VPN

### 正确运行应用的方法:

1. 打开命令提示符或终端
2. 导航到脚本所在目录
3. 输入命令: `streamlit run your_script_name.py`

### 加速处理技巧:

1. **批量处理**:
   - 增加"每批处理记录数"可减少API调用次数
   - 推荐值: 10-20条/批

2. **缓存机制**:
   - 相同文本不会重复调用API
   - 可在侧边栏清除缓存
""")

# 页脚
st.divider()
st.caption("© 2025 清洗服务记录转换工具 | 使用Python和Streamlit构建 | 网络优化版 v3.2")
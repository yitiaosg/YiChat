import sqlite3
import pandas as pd
import os
import time

def inspect_database(db_path):
    # 1. 基础文件信息
    if not os.path.exists(db_path):
        print(f"❌ 错误: 文件 '{db_path}' 不存在。")
        return

    file_size = os.path.getsize(db_path)
    print("="*60)
    print(f"📂 数据库文件名: {os.path.basename(db_path)}")
    print(f"📊 文件大小: {file_size / 1024:.2f} KB")
    print("="*60)

    try:
        conn = sqlite3.connect(db_path)
        # 使 fetch 结果可以通过列名访问
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 2. 获取所有表名
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row['name'] for row in cursor.fetchall()]
        
        if not tables:
            print("Empty Database: 没有发现任何表。")
            return

        for table in tables:
            print(f"\n### 表名: 【{table}】 ###")

            # 3. 获取列名 (PRAGMA table_info)
            cursor.execute(f'PRAGMA table_info("{table}")')
            columns_info = cursor.fetchall()
            col_names = [info['name'] for info in columns_info]
            print(f"📌 列名: {', '.join(col_names)}")

            # 4. 获取索引列 (PRAGMA index_list)
            cursor.execute(f'PRAGMA index_list("{table}")')
            indexes = cursor.fetchall()
            if indexes:
                index_cols = []
                for idx in indexes:
                    cursor.execute(f'PRAGMA index_info("{idx["name"]}")')
                    idx_parts = cursor.fetchall()
                    index_cols.extend([p['name'] for p in idx_parts])
                print(f"🔑 索引列: {', '.join(set(index_cols))}")
            else:
                print("🔑 索引列: (无索引)")

            # 5. 获取数据总量
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            total_rows = cursor.fetchone()[0]
            print(f"📈 总行数: {total_rows}")

            if total_rows == 0:
                print("📝 数据内容: 表为空")
                continue

            # 6. 打印前5行
            print("\n--- 前 5 行 ---")
            cursor.execute(f'SELECT * FROM "{table}" LIMIT 5')
            rows_head = cursor.fetchall()
            for row in rows_head:
                print(dict(row))

            # 7. 打印后5行
            # 注意：SQLite 没有直接的 "TAIL"，所以通常结合 ROWID 或主键反向排序
            if total_rows > 5:
                print("\n--- 后 5 行 ---")
                # 尝试使用通用的 OFFSET 方式获取最后5行
                offset_val = max(0, total_rows - 5)
                cursor.execute(f'SELECT * FROM "{table}" LIMIT 5 OFFSET {offset_val}')
                rows_tail = cursor.fetchall()
                for row in rows_tail:
                    print(dict(row))
            
            print("-" * 40)

    except sqlite3.Error as e:
        print(f"💥 数据库读取出错: {e}")
    finally:
        conn.close()

def clear_db():
    for f in ["cdata.db", "cbal.db"]:
#    for f in ["cdata.db", "cbal.db", "cline.db", "kline.db", "options.db"]:
        with open(f, 'w') as _: pass
        conn = sqlite3.connect(f)
        conn.close()


def db2excel(db_path, name=None, lines=None):
    excel_dir = "/root/Excel"
    
    try:
        if not os.path.exists(excel_dir):
            os.makedirs(excel_dir)
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        if not tables:
            print(f"⚠️ 数据库中没有任何表。")
            conn.close()
            return

        suffix = name if name else ""
        db_name = os.path.basename(db_path).replace('.db', '')
        excel_file = os.path.join(excel_dir, f"{db_name}{suffix}.xlsx")

        # 预检查：先收集有数据的 DataFrame，避免空文件报错
        data_sheets = {}
        for table_name in tables:
            try:
                if isinstance(lines, int) and lines > 0:
                    # 增加引号包裹 "{table_name}"，并增加异常捕获
                    query = f"""
                    SELECT * FROM (
                        SELECT * FROM "{table_name}" ORDER BY ROWID DESC LIMIT {lines}
                    ) ORDER BY ROWID ASC
                    """
                else:
                    query = f'SELECT * FROM "{table_name}"'
                
                df = pd.read_sql_query(query, conn)
                if not df.empty:
                    data_sheets[table_name[:31]] = df
            except Exception as sql_e:
                # 如果 ROWID 失败（针对 WITHOUT ROWID 表），尝试直接读取全部
                if "no such column: ROWID" in str(sql_e):
                    df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
                    if not df.empty:
                        data_sheets[table_name[:31]] = df.tail(lines) if lines else df
                else:
                    print(f"  ❌ 读取表 {table_name} 失败: {sql_e}")

        # 如果没有任何数据可写，直接退出，不创建 ExcelWriter
        if not data_sheets:
            print("⏩ 没有任何数据符合条件，取消导出。")
            conn.close()
            return

        # 写入文件
        with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
            for sheet_name, df in data_sheets.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  📖 已写入 Sheet: {sheet_name}")

        conn.close()
        print(f"✅ 全部导出成功！📍 位置: {excel_file}")
        
    except Exception as e:
        print(f"❌ 导出过程中发生严重错误: {e}")



def str2excel(text, name=None, excel_dir="/storage/emulated/0/Excel", sheet_name="Sheet1"):
    """
    将字符串导出为 Excel

    规则：
    - 输入的每一行，对应 xlsx 里的 1 列
    - 每一行里用 | 分隔的数据，对应该列从上到下的每个单元格

    例如：
        a|b|c
        d|e

    导出后 Excel 中：
        A列: a, d
        B列: b, e
        C列: c, 空
    """
    try:
        if not os.path.exists(excel_dir):
            os.makedirs(excel_dir)

        file_base_name = name if name else "data"
        excel_file = os.path.join(excel_dir, f"{file_base_name}.xlsx")

        # 保留原始空行，不要随便 strip，避免丢数据
        lines = str(text).splitlines()

        if not lines:
            print("⏩ 输入为空，未写入文件")
            return None

        # 每一行拆成一个“列数据”
        columns_data = [line.split("|") for line in lines]

        # 补齐到相同长度，方便转成 DataFrame
        max_len = max(len(col) for col in columns_data)
        data = {}
        for i, col in enumerate(columns_data, start=1):
            padded = col + [""] * (max_len - len(col))
            data[f"col{i}"] = padded

        df = pd.DataFrame(data)

        with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)

        print("✅ 导出成功！")
        print(f"📍 文件位置: {os.path.abspath(excel_file)}")
        return excel_file

    except Exception as e:
        print(f"❌ 导出过程中发生错误: {e}")
        return None


def excel2df(xlsx_name, sheet_name=None):
    """
    读取 Excel 为 DataFrame

    参数：
    - xlsx_name: xlsx 文件名，例如 "test.xlsx" 或 "test"
    - sheet_name: 指定 sheet 名或序号；如果不传，默认读取第一个 sheet
    - excel_dir: Excel 文件夹路径，默认 /storage/emulated/0/Excel

    规则：
    - 只在 Excel 文件夹下找
    - 找不到文件就直接停止并返回 None
    """
    excel_dir="/storage/emulated/0/Excel"
    try:
        # 1. 规范文件名
        if not xlsx_name.lower().endswith(".xlsx"):
            xlsx_name = xlsx_name + ".xlsx"

        excel_path = os.path.join(excel_dir, xlsx_name)

        # 2. 检查文件是否存在
        if not os.path.exists(excel_path):
            print(f"❌ 文件不存在: {excel_path}")
            return None

        # 3. 如果没传 sheet_name，读取第一个 sheet
        if sheet_name is None:
            xls = pd.ExcelFile(excel_path)
            if not xls.sheet_names:
                print("❌ Excel 中没有可读取的 sheet")
                return None
            sheet_name = xls.sheet_names[0]

        # 4. 读取数据
        df = pd.read_excel(
            excel_path,
            sheet_name=sheet_name,
            header=None,
            dtype=object,
            keep_default_na=False
        )

        return df

    except Exception as e:
        print(f"❌ 读取 Excel 失败: {e}")
        return None


def df2str(df):
    """
    将 DataFrame 转回字符串

    规则：
    - DataFrame 的每一列 -> 输出字符串中的一行
    - 列中的每个单元格 -> 用 | 拼接
    """
    try:
        if df is None or df.empty:
            return ""

        lines = []
        for col in df.columns:
            vals = []
            for v in df[col].tolist():
                if pd.isna(v):
                    vals.append("")
                else:
                    vals.append(str(v))

            # 去掉末尾多余空值，避免出现多余的 | 
            while vals and vals[-1] == "":
                vals.pop()

            lines.append("|".join(vals))

        return "\n".join(lines)

    except Exception as e:
        print(f"❌ DataFrame 转字符串失败: {e}")
        return ""

def df2excel(df, name=None, lines=None):
    """
    将 DataFrame 导出为 Excel
    :param df: 要保存的 DataFrame 对象
    :param name: Excel 文件名，默认为 "data"
    :param lines: 指定导出的行数（int，可选），若指定则只导出最后 N 行
    """
    excel_dir = "/root/Excel"
    
    try:
        # 1. 创建目录
        if not os.path.exists(excel_dir):
            os.makedirs(excel_dir)

        # 2. 处理文件名
        file_base_name = name if name else "data"
        excel_file = os.path.join(excel_dir, f"{file_base_name}.xlsx")

        # 3. 核心逻辑：截取行数
        if isinstance(lines, int) and lines > 0:
            df_to_save = df.tail(lines)
            mode_msg = f"最后 {lines} 行"
        else:
            df_to_save = df
            mode_msg = "全部数据"

        # 4. 写入 Excel
        with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
            if not df_to_save.empty:
                # 注意：这里保留了你原代码中的 index=True，如不需要可改为 False
                df_to_save.to_excel(writer, sheet_name="Sheet1", index=True)
                print(f"📖 已写入 Sheet: Sheet1 ({mode_msg})")
            else:
                print(f"⏩ 数据为空，未写入文件")
                return

        print("✅ 导出成功！")
        print(f"📍 文件位置: {os.path.abspath(excel_file)}")

    except Exception as e:
        print(f"❌ 导出过程中发生错误: {e}")




def log_msg(msg):
    ts = time.strftime('%H:%M:%S')

    if not isinstance(msg, pd.DataFrame):
        print(f"[{ts}] {msg}")
        return

    df = msg.copy()
    idx_name = df.index.name or "index"

    def fmt(v):
        if pd.isna(v):
            return "nan"
        return str(v)

    lines = [f"[{ts}]"]
    lines.append("\t".join([f"{idx_name}(index)"] + [str(col) for col in df.columns]))

    for idx, row in df.iterrows():
        line = [fmt(idx)] + [fmt(row[col]) for col in df.columns]
        lines.append("\t".join(line))

    text = "\n".join(lines)
    print(text)
    return text
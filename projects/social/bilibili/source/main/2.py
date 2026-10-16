import pandas as pd

# 加载 CSV 文件
file_path = 'C:/Users/48842/Desktop/B站爬虫/updated_merged_unique_with_subtitles.csv'
data = pd.read_csv(file_path)

# 计算字幕列为空的行数
empty_subtitle_count = data['字幕'].isna().sum()

empty_subtitle_count

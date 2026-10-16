import pandas as pd

# Load the CSV file to check its contents
file_path = '/mnt/data/视频基本信息（手环）.csv'
data = pd.read_csv(file_path)

data.head()

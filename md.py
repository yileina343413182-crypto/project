from markitdown import MarkItDown

md = MarkItDown()

result = md.convert(r'D:\电脑管家迁移文件\xwechat_files\wxid_h5a1s5023l7c12_3ec5\msg\file\2026-05\基于用户听力画像的助听器 自主验配APP设计与实现(1).docx')

print(result.text_content)

output_path = r'd:\毕业设计\taojun.md'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(result.text_content)

print(f'\n已保存至：{output_path}')
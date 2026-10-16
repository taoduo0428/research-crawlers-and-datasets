# useful for handling different item types with a single interface
from itemadapter import ItemAdapter

class Dbfcrs2Pipeline:
    fp = None

    def open_spider(self, spider):
        if spider.name == 'actor':
            self.fp = open('./actor.txt', 'w', encoding='utf-8')
        elif spider.name == 'review':
            self.fp = open('./review.txt', 'w', encoding='utf-8')
        else:
            pass

    def process_item(self, item, spider):
        if spider.name == 'actor':
            actor = item.get('actor', 'None')
            role = item.get('role', 'None')
            gender = item.get('gender', 'None')
            birthday = item.get('birthday', 'None')
            self.fp.write(f"{actor} {role}\n\t{gender}\n\t{birthday}\n")
        elif spider.name == 'review':
            idname = item.get('idname', 'None')
            title = item.get('title', 'None')
            shortcontent = item.get('shortcontent', 'None')
            self.fp.write(f"{idname}\n\t{title}\n\t{shortcontent}\n\n")
        else:
            pass
        return item

    def close_spider(self, spider):
        if self.fp:
            self.fp.close()

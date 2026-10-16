# pipelines.py

from itemadapter import ItemAdapter

class Dbfcrs2Pipeline:
    def open_spider(self, spider):
        self.file = open('movies.txt', 'w', encoding='utf-8')
        self.movies = {}
    
    def close_spider(self, spider):
        for movie in self.movies.values():
            self.file.write(self.format_movie(movie))
        self.file.close()
    
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        name = adapter.get('name', 'None')
        if name not in self.movies:
            self.movies[name] = {
                'name': name,
                'year': adapter.get('year', 'None'),
                'introduce': adapter.get('introduce', 'None'),
                'director': adapter.get('director', 'None'),
                'starring': adapter.get('starring', 'None'),
                'screenwriter': adapter.get('screenwriter', 'None'),
                'actors_details': [],
                'reviews': adapter.get('reviews', None)
            }
        actors_details = self.movies[name]['actors_details']
        for actor in adapter.get('actors_details', []):
            if not any(d['actor'] == actor['actor'] for d in actors_details):
                actors_details.append(actor)
        self.movies[name]['actors_details'] = actors_details
        if self.movies[name]['reviews'] is None:
            self.movies[name]['reviews'] = adapter.get('reviews', None)
        return item
    
    def format_movie(self, movie):
        line = f"Name: {movie['name']}\n"
        line += f"Year: {movie['year']}\n"
        line += f"Introduce: {movie['introduce']}\n"
        line += f"Director: {movie['director']}\n"
        line += f"Starring: {movie['starring']}\n"
        line += f"Screenwriter: {movie['screenwriter']}\n"
        line += "Actors Details:\n"
        for actor in movie['actors_details']:
            line += f"  Actor: {actor.get('actor', 'None')}, Role: {actor.get('role', 'None')}\n"
            for key, value in actor.items():
                if key not in ['actor', 'role']:
                    line += f"    {key}: {value}\n"
        line += "Reviews:\n"
        if movie['reviews'] is None:
            line += "  No reviews found\n"
        else:
            for review in movie['reviews']:
                idname = review.get('idname', 'None')
                title = review.get('title', 'None')
                shortcontent = review.get('shortcontent', 'None')
                line += f"  ID: {idname}, Title: {title}, Content: {shortcontent}\n"
        line += "\n" + "="*50 + "\n\n"
        return line

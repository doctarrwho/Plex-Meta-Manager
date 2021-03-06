import logging, math, re, requests, time
from lxml import html
from modules import util
from modules.util import Failed
from retrying import retry

logger = logging.getLogger("Plex Meta Manager")

class TVDbObj:
    def __init__(self, tvdb_url, language, is_movie, TVDb):
        tvdb_url = tvdb_url.strip()
        if not is_movie and tvdb_url.startswith((TVDb.series_url, TVDb.alt_series_url, TVDb.series_id_url)):
            self.media_type = "Series"
        elif is_movie and tvdb_url.startswith((TVDb.movies_url, TVDb.alt_movies_url, TVDb.movie_id_url)):
            self.media_type = "Movie"
        else:
            raise Failed("TVDb Error: {} must begin with {}".format(tvdb_url, TVDb.movies_url if is_movie else TVDb.series_url))

        response = TVDb.send_request(tvdb_url, language)
        results = response.xpath("//*[text()='TheTVDB.com {} ID']/parent::node()/span/text()".format(self.media_type))
        if len(results) > 0:
            self.id = int(results[0])
        else:
            raise Failed("TVDb Error: Could not find a TVDb {} ID at the URL {}".format(self.media_type, tvdb_url))

        results = response.xpath("//div[@class='change_translation_text' and @data-language='eng']/@data-title")
        if len(results) > 0 and len(results[0]) > 0:
            self.title = results[0]
        else:
            raise Failed("TVDb Error: Name not found from TVDb URL: {}".format(tvdb_url))

        results = response.xpath("//div[@class='row hidden-xs hidden-sm']/div/img/@src")
        self.poster_path = results[0] if len(results) > 0 and len(results[0]) > 0 else None

        tmdb_id = None
        if is_movie:
            results = response.xpath("//*[text()='TheMovieDB.com']/@href")
            if len(results) > 0:
                try:                                                    tmdb_id = util.regex_first_int(results[0], "TMDb ID")
                except Failed as e:                                     logger.error(e)
            if not tmdb_id:
                results = response.xpath("//*[text()='IMDB']/@href")
                if len(results) > 0:
                    try:                                                tmdb_id = TVDb.convert_from_imdb(util.get_id_from_imdb_url(results[0]), language)
                    except Failed as e:                                 logger.error(e)
        self.tmdb_id = tmdb_id
        self.tvdb_url = tvdb_url
        self.language = language
        self.is_movie = is_movie
        self.TVDb = TVDb

class TVDbAPI:
    def __init__(self, Cache=None, TMDb=None, Trakt=None):
        self.Cache = Cache
        self.TMDb = TMDb
        self.Trakt = Trakt
        self.site_url = "https://www.thetvdb.com"
        self.alt_site_url = "https://thetvdb.com"
        self.list_url = "{}/lists/".format(self.site_url)
        self.alt_list_url = "{}/lists/".format(self.alt_site_url)
        self.series_url = "{}/series/".format(self.site_url)
        self.alt_series_url = "{}/series/".format(self.alt_site_url)
        self.movies_url = "{}/movies/".format(self.site_url)
        self.alt_movies_url = "{}/movies/".format(self.alt_site_url)
        self.series_id_url = "{}/dereferrer/series/".format(self.site_url)
        self.movie_id_url = "{}/dereferrer/movie/".format(self.site_url)

    def get_series(self, language, tvdb_url=None, tvdb_id=None):
        if not tvdb_url and not tvdb_id:
            raise Failed("TVDB Error: getget_seriesmove requires either tvdb_url or tvdb_id")
        elif not tvdb_url and tvdb_id:
            tvdb_url = "{}{}".format(self.series_id_url, tvdb_id)
        return TVDbObj(tvdb_url, language, False, self)

    def get_movie(self, language, tvdb_url=None, tvdb_id=None):
        if not tvdb_url and not tvdb_id:
            raise Failed("TVDB Error: get_movie requires either tvdb_url or tvdb_id")
        elif not tvdb_url and tvdb_id:
            tvdb_url = "{}{}".format(self.movie_id_url, tvdb_id)
        return TVDbObj(tvdb_url, language, True, self)

    def get_tvdb_ids_from_url(self, tvdb_url, language):
        show_ids = []
        movie_ids = []
        tvdb_url = tvdb_url.strip()
        if tvdb_url.startswith((self.list_url, self.alt_list_url)):
            try:
                items = self.send_request(tvdb_url, language).xpath("//div[@class='col-xs-12 col-sm-12 col-md-8 col-lg-8 col-md-pull-4']/div[@class='row']")
                for item in items:
                    title = item.xpath(".//div[@class='col-xs-12 col-sm-9 mt-2']//a/text()")[0]
                    item_url = item.xpath(".//div[@class='col-xs-12 col-sm-9 mt-2']//a/@href")[0]
                    if item_url.startswith("/series/"):
                        try:                                                    show_ids.append(self.get_series(language, tvdb_url="{}{}".format(self.site_url, item_url)).id)
                        except Failed as e:                                     logger.error("{} for series {}".format(e, title))
                    elif item_url.startswith("/movies/"):
                        try:
                            tmdb_id = self.get_movie(language, tvdb_url="{}{}".format(self.site_url, item_url)).tmdb_id
                            if tmdb_id:                                             movie_ids.append(tmdb_id)
                            else:                                                   raise Failed("TVDb Error: TMDb ID not found from TVDb URL: {}".format(tvdb_url))
                        except Failed as e:
                            logger.error("{} for series {}".format(e, title))
                    else:
                        logger.error("TVDb Error: Skipping Movie: {}".format(title))
                if len(show_ids) > 0 or len(movie_ids) > 0:
                    return movie_ids, show_ids
                raise Failed("TVDb Error: No TVDb IDs found at {}".format(tvdb_url))
            except requests.exceptions.MissingSchema as e:
                util.print_stacktrace()
                raise Failed("TVDb Error: URL Lookup Failed for {}".format(tvdb_url))
        else:
            raise Failed("TVDb Error: {} must begin with {}".format(tvdb_url, self.list_url))

    @retry(stop_max_attempt_number=6, wait_fixed=10000)
    def send_request(self, url, language):
        return html.fromstring(requests.get(url, headers={"Accept-Language": language}).content)

    def get_items(self, method, data, language, status_message=True):
        pretty = util.pretty_names[method] if method in util.pretty_names else method
        show_ids = []
        movie_ids = []
        if status_message:
            logger.info("Processing {}: {}".format(pretty, data))
        if method == "tvdb_show":
            try:                                                    show_ids.append(self.get_series(language, tvdb_id=int(data)))
            except ValueError:                                      show_ids.append(self.get_series(language, tvdb_url=data))
        elif method == "tvdb_movie":
            try:                                                    movie_ids.append(self.get_movie(language, tvdb_id=int(data)))
            except ValueError:                                      movie_ids.append(self.get_movie(language, tvdb_url=data))
        elif method == "tvdb_list":
            tmdb_ids, tvdb_ids = self.get_tvdb_ids_from_url(data, language)
            movie_ids.extend(tmdb_ids)
            show_ids.extend(tvdb_ids)
        else:
            raise Failed("TVDb Error: Method {} not supported".format(method))
        if status_message:
            logger.debug("TMDb IDs Found: {}".format(movie_ids))
            logger.debug("TVDb IDs Found: {}".format(show_ids))
        return movie_ids, show_ids

    def convert_from_imdb(self, imdb_id, language):
        if self.Cache:
            tmdb_id, tvdb_id = self.Cache.get_ids_from_imdb(imdb_id)
            update = False
            if not tmdb_id:
                tmdb_id, update = self.Cache.get_tmdb_from_imdb(imdb_id)
                if update:
                    tmdb_id = None
        else:
            tmdb_id = None
        from_cache = tmdb_id is not None

        if not tmdb_id and self.TMDb:
            try:                                        tmdb_id = self.TMDb.convert_imdb_to_tmdb(imdb_id)
            except Failed:                              pass
        if not tmdb_id and self.Trakt:
            try:                                        tmdb_id = self.Trakt.convert_imdb_to_tmdb(imdb_id)
            except Failed:                              pass
        try:
            if tmdb_id and not from_cache:              self.TMDb.get_movie(tmdb_id)
        except Failed:                              tmdb_id = None
        if not tmdb_id:                             raise Failed("TVDb Error: No TMDb ID found for IMDb: {}".format(imdb_id))
        if self.Cache and tmdb_id and update is not False:
            self.Cache.update_imdb("movie", update, imdb_id, tmdb_id)
        return tmdb_id

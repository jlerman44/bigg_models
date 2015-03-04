import smtplib
import tornado.ioloop
import tornado.web
import tornado.httpserver
from tornado.options import define, options
from tornado.escape import url_escape
from tornado.web import (StaticFileHandler, RequestHandler, asynchronous,
                         HTTPError)
from tornado.httpclient import AsyncHTTPClient
from tornado import gen
from os.path import abspath, dirname, join
from jinja2 import Environment, PackageLoader
from sqlalchemy.orm import sessionmaker, aliased, Bundle
from sqlalchemy import create_engine, desc, func, or_
from collections import Counter
import simplejson as json
import subprocess
import os

from bigg2 import queries
from bigg2.queries import NotFoundError
from ome import settings
from ome.models import (Model, Component, Reaction,Compartment, Metabolite,
                    CompartmentalizedComponent, ModelReaction, ReactionMatrix,
                    GeneReactionMatrix, ModelCompartmentalizedComponent, ModelGene,
                    Gene, Comments, GenomeRegion, Genome)
from ome.base import Session
from ome.loading.model_loading.parse import split_compartment
import ome

define("port", default= 8888, help="run on given port", type=int)
define("password", default= "", help="password to email", type=str)

# set up jinja2 template location
env = Environment(loader=PackageLoader('bigg2', 'templates'))

# root directory
directory = abspath(dirname(__file__))

# api version
api_v = 'v2'
api_host = 'bigg.ucsd.edu'

# make a tutorial on how to make a api request using curl
# http://www.restapitutorial.com/

# in list display, change so that the each result states what model it is
# from. in other list displays, state the model all the elements are from.  For
# list display, use a dictionary where the n

# -------------------------------------------------------------------------------
# Application API
# -------------------------------------------------------------------------------
def get_application():
    return tornado.web.Application([
        (r'/', MainHandler),
        # 
        # Universal
        # 
        (r'/api/%s/(?:models/)?universal/reactions/?$' % api_v, UniversalReactionListHandler),
        (r'/(?:models/)?universal/reactions/?$', UniversalReactionListDisplayHandler),
        # 
        (r'/api/%s/(?:models/)?universal/reactions/([^/]+)/?$' % api_v, UniversalReactionHandler),
        (r'/(?:models/)?universal/reactions/([^/]+)/?$', UniversalReactionDisplayHandler),
        #
        (r'/api/%s/(?:models/)?universal/metabolites/?$' % api_v, UniversalMetaboliteListHandler),
        (r'/(?:models/)?universal/metabolites/?$', UniversalMetaboliteListDisplayHandler),
        # 
        (r'/api/%s/(?:models/)?universal/metabolites/([^/]+)/?$' % api_v, UniversalMetaboliteHandler),
        (r'/(?:models/)?universal/metabolites/([^/]+)/?$', UniversalMetaboliteDisplayHandler),
        # 
        # By model
        #
        (r'/api/%s/models/?$' % api_v, ModelListHandler),
        (r'/models/?$', ModelsListDisplayHandler),
        # 
        (r'/api/%s/models/([^/]+)/?$' % api_v, ModelHandler),
        (r'/models/([^/]+)/?$', ModelDisplayHandler),
        # 
        (r'/api/%s/models/([^/]+)/reactions/([^/]+)/?$' % api_v, ReactionHandler),
        (r'/models/([^/]+)/reactions/([^/]+)/?$', ReactionDisplayHandler),
        # 
        (r'/api/%s/models/([^/]+)/reactions/?$' % api_v, ReactionListHandler),
        (r'/models/([^/]+)/reactions/?$', ReactionListDisplayHandler),
        #
        (r'/api/%s/models/([^/]+)/metabolites/?$' % api_v, MetaboliteListHandler),
        (r'/models/([^/]+)/metabolites/?$', MetabolitesListDisplayHandler),
        #
        (r'/api/%s/models/([^/]+)/metabolites/([^/]+)/?$' % api_v, MetaboliteHandler),
        (r'/models/([^/]+)/metabolites/([^/]+)/?$', MetaboliteDisplayHandler),
        #
        (r'/api/%s/models/([^/]+)/genes/([^/]+)/?$' % api_v, GeneHandler),
        (r'/models/([^/]+)/genes/([^/]+)/?$', GeneDisplayHandler),
        #
        (r'/api/%s/models/([^/]+)/genes/?$' % api_v, GeneListHandler),
        (r'/models/([^/]+)/genes/?$', GeneListDisplayHandler),
        # 
        # Search
        (r'/api/%s/search$' % api_v, SearchHandler),
        (r'/search$', SearchDisplayHandler),
        (r'/advanced_search$', AdvancedSearchHandler),
        (r'/advanced_search_results$', AdvancedSearchResultsHandler),
        (r'/autocomplete$', AutocompleteHandler),
        #
        # Maps
        (r'/escher_map_json/([^/]+)$', EscherMapJSONHandler),
        #
        # Comments
        (r'/submiterror$', SubmitErrorHandler),
        #
        # Pages
        (r'/sbml$', DownloadPageHandler),
        (r'/web_api$', WebAPIHandler),
        #
        # Static/Download
        (r'/download/(.*)$', DownloadHandler, {'path': join(directory, 'download')}),
        (r'/static/(.*)$', StaticFileHandler, {'path': join(directory, 'static')})
    ], debug=True)

def run(public=True):
    """Run the server"""

    print('Creating pg_trgm extension and indices')
    os.system('psql -d %s -f %s' % (settings.postgres_database, join( directory, 'setup.sql')))
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(get_application())
    print('serving BiGG 2 on port %d' % options.port)
    http_server.listen(options.port, None if public else "localhost")
    try:
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        print("bye!")

def stop():
    """Stop the server"""
    tornado.ioloop.IOLoop.instance().stop()

# -------------------------------------------------------------------------------
# Handlers
# -------------------------------------------------------------------------------

class BaseHandler(RequestHandler):
    pass
    


class MainHandler(BaseHandler):
    def get(self):
        template = env.get_template('index.html')
        self.write(template.render())
        self.set_header('Content-type','text/html')
        self.finish()



class UniversalReactionListHandler(BaseHandler):
    def get(self):
        session = Session()
        universal_reactions = [x[0] for x in session.query(Reaction.bigg_id).all()]
        data = json.dumps(sorted(universal_reactions, key=lambda r: r.lower()))
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()
        session.close()


class UniversalReactionListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/universal/reactions' % (options.port, api_v)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        dictionary = {'results': {'reactions': [{'bigg_id': r, 'model_bigg_id': 'universal'}
                                                for r in json.loads(response.body)]}}
        self.write(template.render(dictionary)) 
        self.set_header('Content-type','text/html')
        self.finish()


class UniversalReactionHandler(BaseHandler):
    def get(self, reaction_bigg_id):
        session = Session()
        # get the reaction object
        try:
            result = queries.get_reaction_and_models(reaction_bigg_id, session)
        except NotFoundError:
            raise HTTPError(404)
        data = json.dumps(result)
        self.write(data)
        self.set_header('Content-type', 'json')
        self.finish()
            

class UniversalReactionDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, reaction_bigg_id):
        print 'UniversalReactionDisplayHandler'
        template = env.get_template("universal_reaction.html")
        http_client = AsyncHTTPClient()
        url_request = ('http://localhost:%d/api/%s/models/universal/reactions/%s' %
                       (options.port, api_v, reaction_bigg_id))
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        self.write(template.render(results))
        self.set_header('Content-type','text/html')
        self.finish()  


class UniversalMetaboliteListHandler(BaseHandler):
    def get(self):
        session = Session()
        metabolites = [x[0] for x in session.query(Metabolite.bigg_id).all()]
        data = json.dumps(sorted(metabolites, key=lambda m: m.lower()))
        session.close()

        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()
        
        
class UniversalMetaboliteListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/universal/metabolites' % \
                      (options.port, api_v)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        template_data = {'results': {'metabolites': [{'bigg_id': r, 'model_bigg_id': 'universal'}
                                                     for r in json.loads(response.body)]}}
        self.write(template.render(template_data)) 
        self.set_header('Content-type','text/html')
        self.finish()
        

class UniversalMetaboliteHandler(BaseHandler):
    def get(self, met_bigg_id):
        session = Session()
        results = queries.get_metabolite(met_bigg_id, session)
        session.close()            

        data = json.dumps(results)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()


class UniversalMetaboliteDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, met_bigg_id):
        template = env.get_template("universal_metabolite.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/universal/metabolites/%s' % \
                      (options.port, api_v, met_bigg_id)
        response = yield gen.Task(http_client.fetch, url_request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        self.write(template.render(results))
        self.set_header('Content-type','text/html')
        self.finish()   
              

class ReactionListHandler(BaseHandler):
    def get(self, model_bigg_id):
        session = Session()
        result = queries.get_reactions_for_model(model_bigg_id, session)
        session.close()

        self.write(json.dumps(result))
        self.set_header('Content-type', 'json')
        self.finish()   
        
        
class ReactionListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, model_bigg_id):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = ('http://localhost:%d/api/%s/models/%s/reactions' %
                       (options.port, api_v, model_bigg_id))
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        response_data = json.loads(response.body)
        template_data = {'results': {'reactions': [{'model_bigg_id': model_bigg_id, 'bigg_id': x}
                                                   for x in response_data]}}
        self.write(template.render(template_data)) 
        self.set_header('Content-type','text/html')
        self.finish()


class ReactionHandler(BaseHandler):
    def get(self, model_bigg_id, reaction_bigg_id):
        session = Session()
        results = queries.get_model_reaction(model_bigg_id, reaction_bigg_id,
                                             session)
        session.close()

        data = json.dumps(results)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()
        

class ReactionDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, modelName, reactionName):
        template = env.get_template("reaction.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s/reactions/%s' % \
                      (options.port, api_v, modelName, reactionName)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        results['reaction_string'] = queries.build_reaction_string(results['metabolites'],
                                                                   results['lower_bound'],
                                                                   results['upper_bound'])
        self.write(template.render(results)) 
        self.set_header('Content-type','text/html')
        self.finish() 

        
class ModelListHandler(BaseHandler):    
    def get(self):
        session = Session()
        model_list = queries.get_model_list_and_counts(session)
        session.close()

        data = json.dumps(model_list)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()


class ModelsListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models' % (options.port, api_v)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        template_data = {'results': {'models': json.loads(response.body)}}
        self.write(template.render(template_data)) 
        self.set_header('Content-type','text/html')
        self.finish()
     

class ModelHandler(BaseHandler):
    def get(self, model_bigg_id):
        session = Session()
        result = queries.get_model_and_counts(model_bigg_id, session)
        session.close()

        data = json.dumps(result)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()


class ModelDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, modelName):
        template = env.get_template("model.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s' % \
                      (options.port, api_v, modelName)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        self.write(template.render(results))
        self.set_header('Content-type','text/html')
        self.finish() 
            

class MetaboliteListHandler(BaseHandler):
    def get(self, model_bigg_id):
        session = Session()
        metaboliteList = queries.get_metabolites_for_model(model_bigg_id, session)
        session.close()
        
        data = json.dumps(sorted(metaboliteList, key=lambda m: m['bigg_id'].lower()))
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()
        
        
class MetabolitesListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, modelName):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s/metabolites' % \
                      (options.port, api_v, modelName)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        dictionary = {"results": {"metabolites": json.loads(response.body)}}
        self.write(template.render(dictionary)) 
        self.set_header('Content-type','text/html')
        self.finish()
 

class MetaboliteHandler(BaseHandler):
    def get(self, model_bigg_id, comp_met_id):
        session = Session()
        met_bigg_id, compartment_bigg_id = split_compartment(comp_met_id)
        results = queries.get_model_comp_metabolite(met_bigg_id, compartment_bigg_id,
                                                    model_bigg_id, session)
        session.close()

        data = json.dumps(results)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()
        

class MetaboliteDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, model_id, met_bigg_id):
        template = env.get_template("metabolite.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s/metabolites/%s' % \
                      (options.port, api_v, model_id, met_bigg_id)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        self.write(template.render(results))
        self.set_header('Content-type','text/html')
        self.finish()   


class GeneListHandler(BaseHandler):
    def get(self, model_bigg_id):
        session = Session()
        results = queries.get_gene_list_for_model(model_bigg_id, session)
        session.close()

        data = json.dumps(sorted(results, key=lambda s: s.lower()))
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()


class GeneListDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, model_bigg_id):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s/genes' % \
                      (options.port, api_v, model_bigg_id)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        template_data = {'results': {'genes': [{'bigg_id': r, 'model_bigg_id': model_bigg_id}
                                                for r in json.loads(response.body)]}}
        self.write(template.render(template_data))
        self.set_header('Content-type','text/html') 
        self.finish()

 
class GeneHandler(BaseHandler):
    def get(self, model_bigg_id, gene_bigg_id):
        session = Session()
        result= queries.get_model_gene(gene_bigg_id, model_bigg_id, session)
        session.close()

        data = json.dumps(result)
        self.write(data)
        self.set_header('Content-type', 'application/json')
        self.finish()


class GeneDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self, modelName, geneId):
        template = env.get_template("gene.html")
        http_client = AsyncHTTPClient()
        url_request = 'http://localhost:%d/api/%s/models/%s/genes/%s' % \
                      (options.port, api_v, modelName, geneId)
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        results = json.loads(response.body)
        self.write(template.render(results))
        self.set_header('Content-type','text/html')
        self.finish() 
        

class SearchHandler(BaseHandler):
    def get(self):
        query_string = self.get_argument("query")

        session = Session()
        # genes
        gene_list = queries.search_for_genes(query_string, session)
        # reactions
        reaction_list = queries.search_for_universal_reactions(query_string,
                                                               session)
        # metabolites
        metabolite_list = []
        metabolite_list += queries.search_for_universal_metabolites(query_string,
                                                                    session)
        metabolite_list += queries.search_for_metabolites(query_string, session,
                                                          strict=True)
        # models
        model_list = queries.search_for_models(query_string, session)
        session.close()

        dictionary = {"results": {"reactions": reaction_list, 
                                  "metabolites": metabolite_list,
                                  "models": model_list,
                                  "genes": gene_list}}
        self.write(json.dumps(dictionary)) 
        self.set_header('Content-type', 'application/json')
        self.finish()

class SearchDisplayHandler(BaseHandler):
    @asynchronous
    @gen.coroutine
    def get(self):
        template = env.get_template("list_display.html")
        http_client = AsyncHTTPClient()
        query_url = url_escape(self.get_argument("query"), plus=True)
        url_request = ('http://localhost:%d/api/%s/search?query=%s' %
                       (options.port, api_v, query_url))
        request = tornado.httpclient.HTTPRequest(url=url_request,
                                                 connect_timeout=20.0,
                                                 request_timeout=20.0)
        response = yield gen.Task(http_client.fetch, request)
        if response.error:
            raise HTTPError(404)
        template_data = json.loads(response.body)
        self.write(template.render(template_data))
        self.set_header('Content-type','text/html')
        self.finish() 
    

class AdvancedSearchHandler(BaseHandler):
    def get(self):
        template = env.get_template('advanced_search.html')
        session = Session()
        model_list = queries.get_model_list(session)
        session.close()

        self.write(template.render({'models': model_list}))
        self.set_header('Content-type','text/html') 
        self.finish()


class AdvancedSearchResultsHandler(BaseHandler):
    def post(self):
        template = env.get_template("list_display.html")
        query_strings = [x.strip() for x in self.get_argument('query', '').split(',') if x != '']

        def checkbox_arg(name):
            return self.get_argument(name, None) == 'on'

        session = Session()
        all_models = queries.get_model_list(session)
        model_list = [m for m in all_models if checkbox_arg(m)]
        include_metabolites = checkbox_arg('include_metabolites')
        include_reactions = checkbox_arg('include_reactions')
        include_genes = checkbox_arg('include_genes')

        metabolite_results = []
        reaction_results = []
        gene_results = []

        # genes
        for query_string in query_strings:
            if include_genes:
                gene_results += queries.search_for_genes(query_string, session,
                                                         limit_models=model_list)
            if include_reactions:
                reaction_results += queries.search_for_reactions(query_string, session,
                                                                 limit_models=model_list)
            if include_metabolites:
                metabolite_results += queries.search_for_metabolites(query_string, session,
                                                                     limit_models=model_list)
        session.close()

        dictionary = {'results': {'reactions': reaction_results, 
                                  'metabolites': metabolite_results, 
                                  'genes': gene_results}}

        self.write(template.render(dictionary)) 
        self.set_header('Content-type','text/html')
        self.finish() 


class AutocompleteHandler(BaseHandler):
    def get(self):
        query_string = self.get_argument("query") 

        # get the session
        session = Session()
        result_array = queries.search_ids_fast(query_string, session)
        session.close()       

        self.write(json.dumps(result_array))
        self.set_header('Content-type', 'application/json')
        self.finish()

class EscherMapJSONHandler(BaseHandler):
    def get(self, map_name):
        session = Session()
        map_json = queries.json_for_map(map_name, session)
        session.close()       
        
        self.write(map_json)
        self.set_header('Content-type', 'application/json')
        self.finish()

class SubmitErrorHandler(BaseHandler):
    def post(self):
        session = Session()
        useremail = self.get_argument("email", "empty")
        comments = self.get_argument("comments", "empty")
        commentobject = Comments(email = useremail, text = comments)
        session.add(commentobject)
        session.commit()
        session.close()
        to = useremail
        gmail_user = 'justinlu10@gmail.com'
        gmail_pwd = options.password
        smtpserver = smtplib.SMTP("smtp.gmail.com", 587)
        smtpserver.ehlo()
        smtpserver.starttls()
        smtpserver.login(gmail_user, gmail_pwd)
        header = ('To:' + gmail_user + '\n' + 'From: ' + gmail_user + '\n' +
                  'Subject:BiGG comment notification\n')
        msg = header + comments + '\n ' + to
        smtpserver.sendmail(gmail_user, gmail_user, msg)
        smtpserver.close()
        
     
class DownloadPageHandler(BaseHandler):
    def get(self):
        template = env.get_template('download.html')
        input = self.get_argument("query")
        model = sbmlio.createSBML(input)
        #currently not implemented. Need script to convert database to coprapy object
        #and then to sbml
        dictionary = {"xml":model.id + ".xml"} 
        self.write(template.render(dictionary))
        self.set_header('Content-type','text/html')
        self.finish()
        
  
class WebAPIHandler(BaseHandler):
    def get(self):
        template = env.get_template('web_api.html')
        self.write(template.render(api_host=api_host))
        self.set_header('Content-type','text/html')
        self.finish()


class DownloadHandler(tornado.web.StaticFileHandler):
    def post(self, path, include_body=True):
        # your code from above, or anything else custom you want to do
        self.set_header('Content-Type','text/xml')  
        self.set_header('Accept-Ranges', 'bytes')  
        self.set_header('Content-Encoding', 'none')  
        self.set_header('Content-Disposition','attachment')
        super(StaticFileHandler, self).get(path, include_body) 


if __name__ == "__main__":
    run()   

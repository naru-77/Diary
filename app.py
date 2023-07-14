from flask import Flask
from flask import render_template, request, redirect, session
from flask_sqlalchemy import SQLAlchemy
import datetime  
from datetime import timedelta
import pytz
from dotenv import load_dotenv
import os
import openai
from flask_login import UserMixin, LoginManager, login_user,logout_user, login_required # flask_loginのインストールが必要
from werkzeug.security import generate_password_hash, check_password_hash
import re #正規表現
import io
from PIL import Image
from stability_sdk import client
import stability_sdk.interfaces.gooseai.generation.generation_pb2 as generation
import math
import base64 # 画像の表示に使う
import random

load_dotenv()

# .envファイルから環境変数を読み込む
openai.api_key = os.getenv('OPENAI_API_KEY') # 以降のopenaiライブラリにはこのAPIを用いる

# 環境変数の設定設定
os.environ['STABILITY_HOST'] = 'grpc.stability.ai:443'
os.environ['STABILITY_KEY'] = '自分のキー'

# ここからDB 



app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SECRET_KEY'] = os.urandom(24) # セッション情報の暗号化のためシークレットキーをランダム生成
db = SQLAlchemy(app)

login_manager = LoginManager() # LoginManagerをインスタンス化
login_manager.init_app(app)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), nullable=True)  # ユーザー名
    post_id = db.Column(db.Integer, nullable=False)  # 投稿ID
    title = db.Column(db.String(50), nullable=False)
    body = db.Column(db.String(300), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.date.today())
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.now(pytz.timezone('Asia/Tokyo')).replace(second=0, microsecond=0)) # 時間の秒以下を無視
    picture = db.Column(db.LargeBinary, default=None)  # 画像のバイナリデータを保存する列
    

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), nullable=True)
    password = db.Column(db.String(12))
    post_count = db.Column(db.Integer, default=0)  # 投稿数を管理するカラム
    
@app.before_request # セッションについての処理追加
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=30)  
    
    
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/', methods=['GET', 'POST']) #自動的にログイン画面へ
def go_login():
    return redirect('/login')

# ユーザー専用ホーム
@app.route('/<username>', methods=['GET', 'POST'])
@login_required # アクセス制限
def home(username):
    user = User.query.filter_by(username=username).first() # ユーザー名でフィルターをかける
    posts = Post.query.filter_by(username=username).all() # ユーザーネームが等しいものをすべて取得

    #絵の取得
    images_dict = {}  
    nums = list(range(1, user.post_count + 1))  # 範囲の数列を作成
    random.shuffle(nums)  # 数列をシャッフル
    for post, num in zip(posts,nums):
        # バイナリデータをImageオブジェクトに変換
        image = Image.open(io.BytesIO(post.picture))
        # 画像データをデータURI形式に変換する
        image_uri = image_to_data_uri(image)
        # 画像のuriとpost_idを紐づけ
        images_dict[num] = image_uri

    return render_template('home.html', posts=posts, username=username,images_dict=images_dict)

        



@app.route('/signup', methods=['GET','POST']) # サインアップ画面
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User(username=username, password=generate_password_hash(password, method='sha256')) # ユーザーをインスタンス化、この時パスワードをハッシュ化
        db.session.add(user)
        db.session.commit()
        return redirect('/login')
    else:
        return render_template('signup.html')


@app.route('/login', methods=['GET','POST']) # ログイン画面
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first() # ユーザー名でフィルターをかける
        if check_password_hash(user.password,password): # ハッシュ化されたパスワードと比較
            login_user(user)
            return redirect(f'/{username}')
        else:
            return redirect('/login')
        
    else:
        return render_template('login.html')
    

@login_manager.unauthorized_handler
def unauthorized():
    return redirect('/login')

# ログアウト
@app.route('/logout')
@login_required # アクセス制限
def logout():
    logout_user()
    return redirect('/login')


@app.route('/<username>/create', methods=['GET','POST']) #ユーザー専用新規作成画面
@login_required # アクセス制限
def create(username):
    if request.method == 'POST':
        title = request.form.get('title')
        body = request.form.get('body')
        input_date = request.form.get('date')
        picture = create_img(body)
        
        return makeDiary(username, title, body, input_date, picture)
    
    else:
        return render_template('create.html', username=username)

@app.route('/<username>/<int:post_id>/update', methods=['GET','POST']) # ユーザー専用編集
@login_required # アクセス制限
def update(post_id,username):
    posts = Post.query.filter_by(username=username).first()
    if(posts != None):
        post = posts.query.get(post_id)
    
        if request.method == 'GET':
            return render_template('update.html', post=post)    
        else:
            post.title = request.form.get('title')
            post.body = request.form.get('body')

            db.session.commit()
            return redirect(f'/{username}')
    
@app.route('/<username>/<int:post_id>/delete', methods=['GET']) # ユーザー専用削除
@login_required # アクセス制限
def delete(post_id,username):
    posts = Post.query.filter_by(username=username).first()
    user = User.query.filter_by(username=username).first()
    if(posts != None):
        post = posts.query.get(post_id)
        user.post_count = user.post_count - 1 # 投稿数を1減らす
        db.session.delete(post)
        db.session.commit()
        return redirect(f'/{username}')

@app.route('/<username>/<int:post_id>/contents', methods=['GET']) # ユーザー専用コンテンツ詳細表示
@login_required # アクセス制限
def contents(post_id,username):
    user = User.query.filter_by(username=username).first() # ユーザー名でフィルターをかける
    if(post_id==0): # 最も古いものから最も新しいものへ
        return redirect(f'/{username}/{user.post_count}/contents')
    elif(post_id==user.post_count+1): # 最も新しいものから最も古いものへ
        return redirect(f'/{username}/{1}/contents')  
    else:
        posts = Post.query.filter_by(username=username).all() # ユーザーネームが等しいものをすべて取得   
        images_dict = {}
    
        for post in posts:
            # バイナリデータをImageオブジェクトに変換
            image = Image.open(io.BytesIO(post.picture))
            # 画像データをデータURI形式に変換する
            image_uri = image_to_data_uri(image)
            # 画像のuriとpost_idを紐づけ
            images_dict[post.post_id] = image_uri

        return render_template('contents.html', posts=posts, user=user,post_id=post_id, images_dict=images_dict)



def image_to_data_uri(image):
    data = io.BytesIO()
    image.save(data, format='PNG')
    data_uri = 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode('utf-8')
    return data_uri

# ここからGPT


# メッセージを保存するリスト
messages = [
    {"role": "system", "content": "あなたは日記を作るためのインタビュアーです。短い質問を1つだけしてください。"},
    {"role": "system", "content": "最初は「今日はどんな一日でしたか？」という質問をしました。"},
]


def query_chatgpt(prompt): # 質問を生成する
    # ユーザーのメッセージをリストに追加
    messages.append({"role": "user", "content": prompt})

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages
    )

    # GPTの応答をリストに追加
    gpt_response = response.choices[0].message.content.strip()
    messages.append({"role": "assistant", "content": gpt_response})

    return gpt_response



def summary_chatgpt(prompt): # 日記をまとめる

    prompt.append({"role": "user", "content": "以上の情報を用いて、日記を作成してください。100字くらいの文章で、見やすさと分かりやすさに気をつけてください。"})

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=prompt
    )

    # GPTの応答をリストに追加
    gpt_response = response.choices[0].message.content.strip()

    return gpt_response

def title_chatgpt(prompt): # タイトルをつける

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "以下の情報を用いて、日記のタイトルを書いてください。10文字程度の体言止めで、見やすさと分かりやすさに気をつけて作ってください。"},{"role": "user", "content": prompt}]
    )

    # GPTの応答をリストに追加
    gpt_response = response.choices[0].message.content.strip()

    return gpt_response


@app.route('/gpt', methods=['POST']) # 質問を作る
def gpt():
    try:
        prompt = request.form.get('speech')
        response = query_chatgpt(prompt)
        return response, 200
    except Exception as e:
        return str(e), 500


def makeDiary(username, title, body, input_date, picture=None): # データベースに日記を登録
    #日付の取得と整合性のチェック
    if re.match(r'\d{4}-\d{2}-\d{2}', input_date): #13月32日みたいなのはhtmlフォーム側で除外してくれる
        date = datetime.datetime.strptime(input_date, '%Y-%m-%d')
    else:
        date = datetime.date.today()
    user = User.query.filter_by(username=username).first()
    user.post_count += 1  # 投稿数を1増やす
    post = Post(username=username ,post_id=user.post_count, title=title, body=body, date=date, picture = picture)
    db.session.add(post)
    db.session.commit()
    return redirect(f'/{username}')


@app.route('/<username>/summary', methods=['POST']) # 日記を作る
def summary(username):
    global messages  # messages をグローバル変数として宣言 chatgptの記憶
    prompt = request.form.get('prompt')
    input_date = request.form.get('date')
    
    messages.append({"role": "user", "content": prompt})

    diary_messages = messages[1:]  # 日記作成に使用するメッセージを取得（最初のシステムメッセージを除く）
    diary_response = summary_chatgpt(diary_messages) # 日記を作成
    diary_title = title_chatgpt(diary_response) # タイトル生成
    diary_picture = create_img(diary_response) # 絵の生成
    messages = [
        {"role": "system", "content": "あなたは日記を作るためのインタビュアーです。短い質問を1つだけしてください。"},
        {"role": "system", "content": "最初は「今日はどんな一日でしたか？」という質問をしました。"},
        ] # GPTの記憶をリセット
    input_date = request.form.get('date')
    
    return makeDiary(username, diary_title, diary_response, input_date, diary_picture)



# 絵を生成する
def create_img(prompt):
    # APIインタフェースの準備
    stability_api = client.StabilityInference(
        key=os.environ['STABILITY_KEY'], 
        verbose=True,
    )
    # chatgptが絵を生成するためのプロンプト生成
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "以下の情報を用いて、その情報を表す手書きで書かれたような絵を生成するようなプロンプトを英語で出してください。プロンプトのみの出力でいいです。"},
        {"role": "user", "content": prompt}]
    )

    width = 500
    height = 350

    adjusted_width = math.ceil(width / 64) * 64 # 64の倍数にしなくてはならない
    adjusted_height = math.ceil(height / 64) * 64 # 64の倍数にしなくてはならない

    # テキストからの画像生成
    answers = stability_api.generate(
        prompt=response.choices[0].message.content.strip(), # プロンプト
        height=adjusted_height,  # 生成される画像の高さを指定
        width=adjusted_width,  # 生成される画像の幅を指定
    )

    # 結果の出力
    for resp in answers:
        for artifact in resp.artifacts:
            if artifact.finish_reason == generation.FILTER:
                print("NSFW")
            if artifact.type == generation.ARTIFACT_IMAGE:
                img = Image.open(io.BytesIO(artifact.binary))
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)
                return buffer.getvalue() # 生成された絵をバイナリデータで返す


if __name__ == '__main__':
    app.run(debug=True)   


from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_required, current_user, login_user, logout_user
from google import genai # IA Gemini
from google.api_core.exceptions import ResourceExhausted
from google.genai.errors import ClientError
import dotenv
import os



# Habría que controlar con try-except los errores al insertar un registro.

dotenv.load_dotenv()
app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tareas.db'
db = SQLAlchemy(app)

login_manager = LoginManager(app)   # Crear objeto login
login_manager.login_message = None  # Indicamos que no envie mensajes (los gestionaremos nosotros)
login_manager.login_view = 'login'  # Indicar la ruta de la función de vista

app.secret_key = os.getenv('FLASK_SECRET_KEY')


# GESTION DE USUARIOS----------------
@login_manager.user_loader
def cargar_usuario(id):
    return db.session.get(Usuario, id)


# CLASES ----------------------------
class Tarea(db.Model):

    id:         db.Mapped[int]  = db.mapped_column(primary_key=True)
    nombre:     db.Mapped[str]  = db.mapped_column(db.String(100), nullable=False)
    completada: db.Mapped[bool] = db.mapped_column(default=False)
    usuario_id: db.Mapped[int]  = db.mapped_column(db.ForeignKey('usuario.id'))

class Usuario(UserMixin,db.Model):
    id:         db.Mapped[int] = db.mapped_column(primary_key=True)
    nombre:     db.Mapped[str] = db.mapped_column(db.String(200), unique=True, nullable=False)
    email:      db.Mapped[str] = db.mapped_column(db.String(200), unique=True, nullable=False)
    password:   db.Mapped[str] = db.mapped_column(db.String(200), nullable=False)


with app.app_context():
    db.create_all()


# ROUTE ----------------------------
@app.route('/', methods=['GET', 'POST'])
@login_required
def inicio():

    print("USER:", current_user)
    print("AUTH:", current_user.is_authenticated)
    print("ID:", current_user.id)
    
    tareas = db.session.scalars(db.select(Tarea).filter_by(usuario_id=current_user.id)).all()
    tareas_totales = len(tareas)
    tareas_pendientes = sum(1 for t in tareas if not t.completada)
    
    return render_template('inicio.html', tareas=tareas, tareas_totales=tareas_totales, tareas_pendientes=tareas_pendientes, pagina="inicio")


# USUARIOS ------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        nombre   = request.form.get("nombre")
        password = request.form.get("password")

        usuario = db.session.scalar( db.select(Usuario).filter_by(nombre=nombre) )  # Scalar espera 1 registro o none -> controlar el none

        # Si no hay usuario
        if not usuario:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("login"))

        # Si hay usuario, checkear pass
        if not check_password_hash(usuario.password, password):
            flash("Password incorrecto", "error")
            return redirect(url_for("login"))

        # Si hay usuario + clave ok
        login_user(usuario)

        flash(f"Bienvenido {usuario.nombre}", "success")
        return redirect(url_for("inicio"))

    return render_template("login.html", pagina="login")


@app.route('/logout', methods=['POST'])
@login_required
def logout():

    logout_user()
    
    current_user.is_authenticated == False   # En teoría no necesario

    flash("Sesión cerrada", "success")
    return redirect(url_for("login"))


@app.route('/mis_datos')
@login_required
def mis_datos():

    return render_template("mis_datos.html", pagina="mis_datos")


@app.route('/registro', methods=['GET', 'POST'])
def registro():

    # En este módulo si no se pasan las validadiones se redirige a 'registro' por lo que el formulario
    # queda con los datos vacíos.
    # La solución sería hacer un return render_template("registro.html", pagina="registro", nombre=nombre, email=email)
    # pero en tal caso en el registro.htmal habría que utilizar esos datos (excepto en el password)
    # <input type="text" name="nombre" value="{{ nombre or '' }}">
    # <input type="email" name="email" value="{{ email or '' }}">
    # <input type="password" name="password">

    if request.method == 'POST':

        nombre      = request.form.get( "nombre"   )
        email       = request.form.get( "email"    )
        password    = request.form.get( "password" )

        # Validaciones de campos obligatorios
        if not nombre or not nombre.strip():
            flash("El nombre es obligatorio", "error")
            return redirect(url_for("registro"))

        if not email or not email.strip():
            flash("El email es obligatorio", "error")
            return redirect(url_for("registro"))

        if not password:
            flash("La contraseña es obligatoria", "error")
            return redirect(url_for("registro"))

        # Validaciones ok
        nombre = nombre.strip()
        email  = email.strip()

        # Comprobar si el usuario ya existe
        usuario_existe = db.session.scalar(db.select(Usuario).filter_by(nombre=nombre))  # Espero 1 resultado o none -> controlar none

        if usuario_existe:      # Si ya existe
            flash(f"El usuario '{nombre}' ya existe","error")
            return redirect(url_for("registro"))
        
        # Comprobar si el mail ya existe
        email_existe = db.session.scalar(db.select(Usuario).filter_by(email=email))  # Espero 1 resultado o none -> controlar none

        if email_existe:      # Si ya existe
            flash(f"El email '{email}' ya está registrado","error")
            return redirect(url_for("registro"))
        
        # Crear usuario
        nuevo_usuario = Usuario(nombre=nombre, email=email, password=generate_password_hash(password))
        db.session.add(nuevo_usuario)
        db.session.commit()

        flash(f"Usuario '{nombre}' creado","success")
        return redirect(url_for("inicio"))

    return render_template("registro.html", pagina="registro")


# TAREAS ----------------------------
@app.route('/nueva', methods=['POST'])
@login_required
def nueva():

    modo = request.form.get("modo")
    nombre = request.form.get("nombre")

    if modo == "inteligente":

        try: # Para controlar errores en IA

            gemini = genai.Client()
            respuesta = gemini.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=f"Dame una lista de 5 tareas (sin numerar) para {nombre} sin añadir"
                            " encabezados a tu respuesta, y de 50 caracteres de máximo cada una" )

            # Validar respuesta
            if not respuesta or not getattr(respuesta, "text", None):
                raise ValueError("Respuesta vacía de la IA")
            
            lineas = respuesta.text.split("\n")
            nuevas_tareas = [ Tarea(nombre=linea.strip(),usuario_id=current_user.id) for linea in lineas if linea.strip() and len(linea.strip()) > 3]
 
            # Validar que hay alguna tarea
            if not nuevas_tareas:
                raise ValueError("La IA no devolvió tareas válidas")
            
            db.session.add_all(nuevas_tareas)
            db.session.commit()
                
            flash("Tareas generadas por IA añadidas","success")

                
        except ClientError as e:
            db.session.rollback()

            if e.code == 429:
                flash("Te pasaste de peticiones. Inténtalo otra vez, pero hoy no... MAÑAAAAANA !!", "error")
            else:
                flash("Error en el servicio de IA.", "error")
            
       
    if modo =="manual":
        if nombre and nombre.strip():
            nueva_tarea = Tarea(nombre=nombre,usuario_id=current_user.id)
            db.session.add(nueva_tarea)
            db.session.commit()
            flash(f"Tarea '{nombre}' creada","success")

    return redirect(url_for("inicio"))


@app.route('/completar/<int:id>', methods=['POST'])
@login_required
def completar(id):

    tarea = db.session.scalar(db.select(Tarea).filter_by(id=id, usuario_id=current_user.id)) # Scalar espera 1 registro o none -> controlar none

    if not tarea:
        abort(404)

    nombre = tarea.nombre.lower() # Para detectar "reto" como sea

    # No completar  SI CONTIENE "reto"
    if "reto" in nombre:
        flash(f"Es IMPOSIBLE '{tarea.nombre}' ...y lo sabes... 😏", "error")
        return redirect(url_for("inicio"))

    tarea.completada = not tarea.completada

    db.session.commit()

    return redirect(url_for('inicio'))


@app.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar(id):
    
    #tarea = Tarea.query.filter_by(id=id,usuario_id=current_user.id).first_or_404()
    tarea = db.session.scalar( db.select(Tarea).filter_by(id=id,usuario_id=current_user.id) )
    #Scalar espera 1 o none -> habría que comprobar que no es none antes de eliminar

    nombre = tarea.nombre

    db.session.delete(tarea)
    db.session.commit()

    flash(f"Tarea '{nombre}' eliminada","success")

    return redirect(url_for("inicio"))


@app.route('/datos')
def datos():

    valor = 55
    nombre = "Carlos"
    
    return render_template('datos.html',edad=valor, nombre=nombre, pagina="datos")


@app.route('/about')
def about():

    desarrollador = "Carlos"
    version = "1.1"
    
    return render_template('about.html',desarrollador=desarrollador, version=version, pagina="about")


if __name__ == '__main__':

    app.run(debug=True, use_reloader=False)




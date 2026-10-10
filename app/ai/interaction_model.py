import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

FEATURES=['duration_seconds','errors','steps','back_actions']
LABELS={0:'Interacción estable',1:'Interacción con algunas dificultades',2:'Interacción más compleja'}

def cluster_interactions(sessions):
    """Entrena K-Means sobre métricas de interacción; devuelve None si faltan datos variados."""
    rows=[]
    for session in sessions:
        rows.append({'duration_seconds':float(session.duration_seconds or 0),'errors':float(session.errors or 0),
                     'steps':float(session.steps or 0),'back_actions':float(session.back_actions or 0)})
    if len(rows)<3:return None
    frame=pd.DataFrame(rows,columns=FEATURES).replace([float('inf'),float('-inf')],0).fillna(0)
    scaled=StandardScaler().fit_transform(frame)
    unique=len(set(map(tuple,scaled)))
    if unique<2:return None
    k=min(3,unique,len(rows))
    model=KMeans(n_clusters=k,random_state=42,n_init=10)
    clusters=model.fit_predict(scaled)
    # Interpreta cada grupo por su puntuación media (solo dificultad de navegación).
    scores=frame['errors']*3+frame['back_actions']*2+frame['steps']*.25+frame['duration_seconds']/60
    means={int(c):float(scores[clusters==c].mean()) for c in set(clusters)}
    ordered=sorted(means,key=means.get)
    mapped={cluster:LABELS[min(i,2)] for i,cluster in enumerate(ordered)}
    return [{'cluster_id':int(c),'label':mapped[int(c)]} for c in clusters]

"""Bounded read-only STL inspection; not a slicer or repair engine."""
import argparse,html,json,math,re,struct,sys
from collections import Counter,defaultdict
from local_io import InputError,need,workspace,local_path,read_bytes,sha,canonical,deliver
LIMIT=16*1024*1024
MAX_FACES=30000
NUM=re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')

def vec(values):
    need(len(values)==3,'VECTOR_LENGTH')
    out=tuple(float(x) for x in values)
    need(all(math.isfinite(x) and abs(x)<=1e12 for x in out),'COORDINATE_RANGE')
    return out

def parse_stl(raw,mode='auto'):
    need(len(raw)<=LIMIT,'FILE_LIMIT')
    need(mode in ('auto','ascii','binary'),'FORMAT_INVALID')
    binary_size=len(raw)>=84 and len(raw)==84+50*struct.unpack_from('<I',raw,80)[0]
    if mode=='binary' or (mode=='auto' and binary_size):
        need(binary_size,'BINARY_LENGTH_MISMATCH')
        n=struct.unpack_from('<I',raw,80)[0];need(1<=n<=MAX_FACES,'FACE_COUNT_RANGE')
        rows=[]
        for i in range(n):
            a=struct.unpack_from('<12fH',raw,84+50*i)
            rows.append((vec(a[:3]),tuple(vec(a[j:j+3]) for j in (3,6,9)),a[12]))
        return 'binary',rows
    try:text=raw.decode('ascii')
    except UnicodeError as e:raise InputError('ASCII_ENCODING_OR_INVALID_BINARY') from e
    need(all(c in '\r\n\t' or 32<=ord(c)<=126 for c in text),'ASCII_CONTROL')
    lines=[s.strip() for s in text.splitlines() if s.strip()]
    need(len(lines)>=9 and re.fullmatch(r'solid(?:[ \t].*)?',lines[0]) is not None,'ASCII_SOLID')
    need(re.fullmatch(r'endsolid(?:[ \t].*)?',lines[-1]) is not None,'ASCII_ENDSOLID')
    need((len(lines)-2)%7==0,'ASCII_FACET_STRUCTURE')
    n=(len(lines)-2)//7;need(1<=n<=MAX_FACES,'FACE_COUNT_RANGE')
    rows=[]
    def values(line,prefix):
        parts=line.split();need(parts[:len(prefix)]==prefix and len(parts)==len(prefix)+3,'ASCII_VECTOR_SYNTAX')
        v=parts[len(prefix):];need(all(len(x)<=64 and NUM.fullmatch(x) for x in v),'ASCII_NUMBER')
        return vec(v)
    for start in range(1,len(lines)-1,7):
        x=lines[start:start+7]
        need(x[1]=='outer loop' and x[5]=='endloop' and x[6]=='endfacet','ASCII_FACET_STRUCTURE')
        rows.append((values(x[0],['facet','normal']),tuple(values(x[j],['vertex']) for j in (2,3,4)),0))
    return 'ascii',rows

def inspect(raw,mode='auto'):
    kind,rows=parse_stl(raw,mode)
    vertices={};faces=[];normal_zero=[];normal_opposed=[];degenerate=[];attrs=[]
    bounds=[[float('inf'),-float('inf')] for _ in range(3)]
    for index,(normal,points,attr) in enumerate(rows,1):
        ids=[]
        for p in points:
            if p not in vertices:vertices[p]=len(vertices)
            ids.append(vertices[p])
            for d in range(3):bounds[d]=[min(bounds[d][0],p[d]),max(bounds[d][1],p[d])]
        faces.append(tuple(ids))
        u=[points[1][d]-points[0][d] for d in range(3)];v=[points[2][d]-points[0][d] for d in range(3)]
        cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        if cross==(0,0,0):degenerate.append(index)
        if normal==(0,0,0):normal_zero.append(index)
        elif sum(cross[d]*normal[d] for d in range(3))<0:normal_opposed.append(index)
        if attr:attrs.append(index)
    degset=set(degenerate);edge=defaultdict(list);same=defaultdict(list)
    parent=list(range(len(rows)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def join(a,b):parent[find(b)]=find(a)
    for index,f in enumerate(faces,1):
        same[tuple(sorted(f))].append(index)
        if index in degset:continue
        for a,b in zip(f,f[1:]+f[:1]):edge[tuple(sorted((a,b)))].append((index,1 if a<b else -1))
    boundary=[];nonmanifold=[];orientation=[]
    for key,inc in edge.items():
        ids=[x[0] for x in inc]
        for i in ids[1:]:join(ids[0]-1,i-1)
        item={'vertex_ids':[key[0]+1,key[1]+1],'face_ids':ids}
        if len(inc)==1:boundary.append(item)
        elif len(inc)>2:nonmanifold.append(item)
        elif inc[0][1]==inc[1][1]:orientation.append(item)
    duplicates=[ids for ids in same.values() if len(ids)>1]
    components=len({find(i) for i in range(len(rows)) if i+1 not in degset})
    stats={'triangles':len(rows),'exact_vertices':len(vertices),'numerical_zero_area_faces':len(degenerate),
           'duplicate_face_groups':len(duplicates),'duplicate_extra_faces':sum(len(g)-1 for g in duplicates),
           'boundary_edges':len(boundary),'over_two_face_edges':len(nonmanifold),'same_direction_two_face_edges':len(orientation),
           'edge_connected_components_excluding_zero_area':components,'zero_stored_normals':len(normal_zero),
           'opposed_stored_normals':len(normal_opposed),'nonzero_binary_attribute_faces':len(attrs)}
    issues={'numerical_zero_area_faces':degenerate,'duplicate_face_groups':duplicates,'boundary_edges':boundary,
            'over_two_face_edges':nonmanifold,'same_direction_two_face_edges':orientation,
            'zero_stored_normals':normal_zero,'opposed_stored_normals':normal_opposed,'nonzero_binary_attribute_faces':attrs}
    return {'format':kind,'stats':stats,'bounds_native_units':[[format(x,'.17g') for x in b] for b in bounds],
            'unit':'UNSPECIFIED_BY_STL','coordinate_matching':'EXACT_PARSED_FLOAT_NO_WELDING',
            'issues':{k:{'total':len(v),'first_100':v[:100]} for k,v in issues.items()},
            'printability':'NOT_VERIFIED','self_intersections':'NOT_CHECKED','vertex_manifoldness':'NOT_CHECKED',
            'note':'边按非数值零面积面统计；重复面保留，计数可相互影响。面和顶点编号从1开始，顶点按首次出现顺序。'}

LABELS={'triangles':'三角面','exact_vertices':'精确坐标顶点','numerical_zero_area_faces':'数值零面积面','duplicate_face_groups':'重复面组',
        'duplicate_extra_faces':'重复多余面','boundary_edges':'边界边','over_two_face_edges':'超过两面共用的边',
        'same_direction_two_face_edges':'两面同向边','edge_connected_components_excluding_zero_area':'按边连通分组（排除零面积面）',
        'zero_stored_normals':'文件法向量为零的面','opposed_stored_normals':'文件法向量与顶点方向相反的面','nonzero_binary_attribute_faces':'含非零扩展属性的面'}
def render(report):
    r=report['result'];e=html.escape
    rows=''.join('<tr><th>'+LABELS[k]+'</th><td>'+str(v)+'</td></tr>' for k,v in r['stats'].items())
    detail=''.join('<h3>'+e(k)+'</h3><pre>'+e(json.dumps(v,ensure_ascii=False,indent=2))+'</pre>' for k,v in r['issues'].items() if v['total'])
    bounds=' / '.join('XYZ'[i]+': '+', '.join(b) for i,b in enumerate(r['bounds_native_units']))
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>STL模型网格预检</title>'
      '<style>body{font:16px/1.6 system-ui;background:#edf5f8;color:#16394a;max-width:1000px;margin:30px auto;padding:24px}table{background:white;border-collapse:collapse;width:100%}td,th{border:1px solid #bbcdd6;padding:8px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}.warn{background:#fff1cf;padding:16px}</style>'
      '<h1>STL模型网格预检</h1><p class="warn">仅结构预检，不是可打印认证。没有发现这些问题也不能保证模型封闭、无自交或可安全使用。原模型未修改。</p>'
      '<p>格式：'+e(r['format'])+'；单位：STL未指定，请在建模和切片软件核对。</p><p>'+e(bounds)+'</p><table>'+rows+'</table>'
      '<p>'+e(r['note'])+'</p><p>精确浮点坐标匹配，不合并近邻点。尺寸以原始坐标单位展示，不推定毫米，不检查壁厚、支撑、碰撞、自交或顶点流形。扩展属性不解码为颜色。所有问题列表最多显示前100项。</p>'+detail+'</html>').encode()

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--format',choices=['auto','ascii','binary'],default='auto');p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    try:
        root=workspace(a.workspace);src=local_path(root,a.input);out=local_path(root,a.out,output=True)
        raw=read_bytes(src,LIMIT);result=inspect(raw,a.format)
        report={'run_id':sha(raw+canonical({'format':a.format}))[:24],'inputs':{'model':{'sha256':sha(raw),'bytes':len(raw)}},'result':result}
        if a.check:print(json.dumps({'state':'INPUT_CHECKED_NO_OUTPUT','stats':result['stats']},ensure_ascii=False));return 0
        captures={'model':{'path':src,'raw':raw,'limit':LIMIT}}
        deliver(root,out,report,{'audit.json':canonical(report),'report.html':render(report)},captures)
        print(json.dumps({'state':'REPORT_COMPLETE','printability':'NOT_VERIFIED','stats':result['stats']},ensure_ascii=False));return 0
    except (InputError,OSError,ValueError,OverflowError) as e:print(json.dumps({'state':'FAILED','error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':sys.exit(main())

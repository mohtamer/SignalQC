# hk_nn.py
# Hussein/Kishida neural model (JMP export → pure NumPy), ready for reticulate.

from math import tanh
import numpy as np

EPS = 1e-20  # for safe logs/divisions

# ---- (Optional) metadata helpers ----
def getModelMetadata():
    return {
        "creator": "Neural",
        "modelName": "Hussein/Kishida NN",
        "predicted": ["fcHPmanual", "fcLPmanual"],
        "table": "Filter_v4",
        "version": "18.0.0",
        "timestamp": "2025-10-01T15:00:42Z",
    }

def getInputMetadata():
    return {
        "ln_ratio_FASave_over_FASnave_0.0018": "float",
        "ln_ratio_FASave_over_FASnave_0.0056": "float",
        "ln_ratio_FASave_over_FASnave_0.0178": "float",
        "ln_ratio_FASave_over_FASnave_0.0562": "float",
        "ln_ratio_FASave_over_FASnave_0.1778": "float",
        "ln_ratio_FASave_over_FASnave_0.5623": "float",
        "ln_ratio_FASave_over_FASnave_1.7783": "float",
        "ln_ratio_FASave_over_FASnave_17.7828": "float",
        "ln_ratio_FASave_over_FASnave_5.6234": "float",
    }

def getOutputMetadata():
    return {"fcHPmanual": "float", "fcLPmanual": "float"}


# ---- utilities ----
def _gauss(x):
    # matches JMP: exp(-0.5 * pow(linear_combo, 2))
    x = float(x)
    return np.exp(-0.5 * (x ** 2))


# ---- core scorer: inputs -> {"fcHP": h, "fcLP": l} ----
def score(indata):
    """
    Parameters
    ----------
    indata : dict
      Keys: ln_ratio_FASave_over_FASnave_{freq} (freq in Hz)
      Values: float

    Returns
    -------
    dict with keys {"fcHP", "fcLP"}
    """
    # local alias to keep expressions readable
    r = lambda f: float(indata[f])

    H1_1  = tanh((1.23029370464362 + -0.0108118332661891*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.137625949006819*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.186596988019409*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.0492123789236512*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.257513311393434*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.116480106224129*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0562871670414646*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0504455421987934*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0142360990159464*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_2  = (0.675155160048692 + -0.311434691971802*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.312798445322322*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0835351937115478*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.00263313326919328*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.149214508637819*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.0819724974910783*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.044530905512642*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.17430191079829*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.118658718578466*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_3  = (0.754616560628116 + 0.136408783397355*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.0482068560228755*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0985287366015595*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.000165867332859208*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.0117150067564654*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.0681976913284148*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.233482187440616*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.184400793842095*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0849046821434042*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_4  = (0.355472088948757 + -0.110962879354445*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.194497804216757*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.00561660914193766*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.179564008512683*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0551419793155303*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0946555292351772*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.0363449887979054*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0658913682961425*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0801022132947742*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_5  = (-2.497883031923 + 0.782760590339116*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.130551276334827*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0502060090048966*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.149826078002326*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0249134320047137*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.188906943730825*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.151521719722922*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.136756241119006*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.055034762607753*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_6  = (-2.45893137978712 + -0.295269581417037*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.0299681791021608*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.139635522462839*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.00863938882433552*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.204904889701341*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.19028644667562*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.285654931934838*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.145804822590254*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.00358393969925653*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_7  = (-0.561162169390465 + 0.0799743944263385*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.348570099188709*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.0821187852640749*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.143210382063866*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0347318427347097*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.131390801729921*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.043524465638306*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.253744881235214*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.293710238621002*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_8  = (-1.45769499096075 + -0.350734853065434*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.166039758789291*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.0548678028458376*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.0797937911849969*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.0527209727742963*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.10502788965845*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.404444848884207*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.026420899962597*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.0755790843115592*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_9  = (0.617997768723781 + 0.249458924624844*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.30489272548247*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0467625812233697*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.103254357600222*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.0263572849746846*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.132981890172446*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.125459141514588*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.100078643467834*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.0434578461491472*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_10 = (-1.23093748981826 + 0.117036577531083*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.030157048936935*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0546402675889316*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.0880653342204608*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.138006844808333*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.313388706324766*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.166371354783865*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.336237730619623*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.148368764376658*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_11 = (1.25519685293838 + -0.246555133755723*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.0387586884094898*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0179304985201626*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.0337842211709729*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.0270489501935854*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.134237382494131*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0911827517837762*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.169291761264472*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0884254985027368*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_12 = (1.09875703235181 + -0.263962823759368*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.198190510167506*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0815801236049601*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.196004561645757*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0400653074659772*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.0938560468964172*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0420297205151461*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0379464759773638*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.124966207324892*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_13 = (0.100911198661072 + -0.0221414791363887*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.109013707362578*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.00447583275611499*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.0684905679889379*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.106171055422038*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0685315100303077*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.00373624459770895*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0422195825337056*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.0322778093177057*r("ln_ratio_FASave_over_FASnave_5.6234"))
    H1_14 = _gauss((-1.96122334947942 + 0.199988546327767*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.335013665958362*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.780048037964585*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.333340047748329*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.359322884102996*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0555280597625459*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.104314800859813*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.126148671135589*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0275542545161726*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_15 = _gauss((-8.78756350109341 + 0.282983860182197*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.704745095701585*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.45561526361315*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.134555223257539*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0428343445881345*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.521167310811895*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.599250912444228*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.513307065880381*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.562411617681059*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_16 = _gauss((5.10892032550478 + -0.159437606825516*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.0894079105392312*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.0934527114061089*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.304001241745169*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.358100782375895*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.273693198260774*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.24666768037683*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.165780028809882*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.250461110217216*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_17 = _gauss((-3.92652967052445 + 0.224794203907824*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.998793504465713*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.573255548709339*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.287996783600634*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.321320791514214*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.078000109212947*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.0611285821259787*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.513486374029798*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.0211164618198796*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_18 = _gauss((-1.10637432413197 + 0.106642048780571*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.218494807946141*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.246400027293411*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.204754413329995*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.00601726863155192*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.062923500226468*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.226912121428422*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.32039709170222*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.12709755052619*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_19 = _gauss((1.00136808021282 + 0.354395390784399*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.228608481983979*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.515805182744297*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.253853419408843*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0117838828587729*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0373635269795089*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.0126166876576998*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.584295291421332*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.244183638396472*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_20 = _gauss((-1.75529420746714 + -0.0416792788049144*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.0520478531838474*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.0888029327339757*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.190257336713427*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0480494059646194*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.326860284369656*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.0246963883993771*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.216442347849117*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.193042026497392*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_21 = _gauss((3.96072637306964 + -0.225798545987481*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.126758752974569*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.503238009731861*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.21953855693475*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.537107049689234*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.306232069178738*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0986327704337554*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.186493276456113*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.0563244529866663*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_22 = _gauss((0.362500273740131 + -0.788287996823542*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.53400569627685*r("ln_ratio_FASave_over_FASnave_0.0056") + 1.44114496050728*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.375659763712939*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.431847851398636*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.0254954584032143*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0691927990069721*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.21968154509168*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.333430297868185*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_23 = _gauss((-0.0598190902537513 + -0.223029913931323*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.655646125935229*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.392338146592935*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.548312488528428*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.347560102597439*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.072074034553942*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.223971868245154*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.0668280624794985*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.213621723241377*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_24 = _gauss((0.2347676775147 + -0.246652338288618*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.204530478794567*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.382660495343678*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.230640545757913*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.0335929515702278*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0352260664040219*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.0616112247447862*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.026283077523046*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0356141394985089*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_25 = _gauss((-3.05330619879944 + 0.0123278929208986*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.177028810344268*r("ln_ratio_FASave_over_FASnave_0.0056") + -1.17347598750641*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.5134708922176*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.429283110776133*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.300129421855932*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.191498139640043*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.762354378224226*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.148212788627642*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_26 = _gauss((-6.19375257191717 + 1.03270586486285*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.0811898438419677*r("ln_ratio_FASave_over_FASnave_0.0056") + 1.1890278598486*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.0942939240513197*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.41316856906567*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.106998867064286*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.167740943934828*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0290565727317052*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.560419800892912*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_27 = _gauss((-2.83608651771715 + 0.0597873939320074*r("ln_ratio_FASave_over_FASnave_0.0018") + -0.142956635275922*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.0201144503152863*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.287340498770052*r("ln_ratio_FASave_over_FASnave_0.0562") + 0.135443803951913*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.103614866397559*r("ln_ratio_FASave_over_FASnave_0.5623") + 0.404286130608623*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0929928762003794*r("ln_ratio_FASave_over_FASnave_17.7828") + 0.285228199012967*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_28 = _gauss((5.36452437703941 + -0.265775020788074*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.385793557125798*r("ln_ratio_FASave_over_FASnave_0.0056") + 0.313036264743638*r("ln_ratio_FASave_over_FASnave_0.0178") + -0.103430419370535*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.438499209031352*r("ln_ratio_FASave_over_FASnave_0.1778") + -0.673578622214105*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.681289150697745*r("ln_ratio_FASave_over_FASnave_1.7783") + 0.667482305833732*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0137703629589954*r("ln_ratio_FASave_over_FASnave_5.6234")))
    H1_29 = _gauss((-0.1073208652547 + 0.301863848840888*r("ln_ratio_FASave_over_FASnave_0.0018") + 0.465420915744481*r("ln_ratio_FASave_over_FASnave_0.0056") + -0.079621267670671*r("ln_ratio_FASave_over_FASnave_0.0178") + 0.0381017964731126*r("ln_ratio_FASave_over_FASnave_0.0562") + -0.0835018048158922*r("ln_ratio_FASave_over_FASnave_0.1778") + 0.0375519815938769*r("ln_ratio_FASave_over_FASnave_0.5623") + -0.0594606852132345*r("ln_ratio_FASave_over_FASnave_1.7783") + -0.0297463450026489*r("ln_ratio_FASave_over_FASnave_17.7828") + -0.0527804568368806*r("ln_ratio_FASave_over_FASnave_5.6234")))

    fcHP = (
        0.751517181579864 + 0.251438786992051*H1_1 + -0.00711068085978224*H1_10 +
        -0.383080243958919*H1_11 + 0.00514678310674018*H1_12 + -0.00189941180670408*H1_13 +
        0.0542496337990457*H1_14 + 0.0639607218916115*H1_15 + 0.108044898701555*H1_16 +
        0.01467321848282*H1_17 + -0.264137464829464*H1_18 + -0.0974131012313662*H1_19 +
        -0.173319028328559*H1_2 + -0.095005548277834*H1_20 + -0.218152394560138*H1_21 +
        0.182791668580419*H1_22 + 0.0884443748094717*H1_23 + 0.00791439185627513*H1_24 +
        0.0322561805746057*H1_25 + 0.0252543037083836*H1_26 + -0.283938638162197*H1_27 +
        -0.0898758632472355*H1_28 + 0.0870336293106873*H1_29 + 0.387795153561535*H1_3 +
        0.14223647191187*H1_4 + -0.29037104434387*H1_5 + 0.00141120084174073*H1_6 +
        -0.0316936124131106*H1_7 + 0.242006692039191*H1_8 + 0.408484685122746*H1_9
    )

    fcLP = (
        19.7356400247932 + -6.64871044609534*H1_1 + 6.20251306427176*H1_10 +
        -0.0000602260202334899*H1_11 + -0.0000777817879295189*H1_12 + 0.0000388379788514208*H1_13 +
        6.94090250782118*H1_14 + -1.55842147695305*H1_15 + 3.68185245727126*H1_16 +
        -3.04491856855456*H1_17 + 2.17274035631073*H1_18 + -1.78692761398708*H1_19 +
        -13.5210360989232*H1_2 + -12.1323481530678*H1_20 + -0.0183281068455121*H1_21 +
        -0.567301553320978*H1_22 + -0.213160785102017*H1_23 + -0.124368966411378*H1_24 +
        0.116533043914904*H1_25 + -2.06840570182011*H1_26 + 5.82687147389638*H1_27 +
        3.40503723717081*H1_28 + 2.32517244698756*H1_29 + 14.4769874640988*H1_3 +
        -5.24445140665188*H1_4 + -2.86601014792102*H1_5 + -7.13207597899251*H1_6 +
        -11.4028185854491*H1_7 + 26.6172002837214*H1_8 + 9.82072197157278*H1_9
    )

    # Optional gentle clamps (comment in if you want safety bounds)
    # fcHP = max(0.001, float(fcHP))
    # fcLP = max(fcHP * 1.05, min(50.0, float(fcLP)))

    return {"fcHP": float(fcHP), "fcLP": float(fcLP)}


# ---- feature builder from arrays (log-log interpolation) ----
_ANCHORS = np.array([0.0018, 0.0056, 0.0178, 0.0562, 0.1778, 0.5623, 1.7783, 17.7828, 5.6234], dtype=float)

def compute_ln_ratio_features(freq, FAS_ave, FASn_ave):
    """
    Build the 9 required ln(FAS.ave / FASn.ave) features at the anchor frequencies.

    freq, FAS_ave, FASn_ave : 1D arrays, same length.
       - 'FAS_ave' = median FAS of the ENTIRE series (your definition)
       - 'FASn_ave' = median FAS of the NOISE window
    Returns : dict keyed exactly as the scorer expects.
    """
    f = np.asarray(freq, dtype=float)
    s = np.asarray(FAS_ave, dtype=float)
    n = np.asarray(FASn_ave, dtype=float)

    # protect against non-positive values before log space
    s = np.maximum(s, EPS)
    n = np.maximum(n, EPS)

    # log-log interpolation to anchors
    logf = np.log(f)
    logs = np.log(s)
    logn = np.log(n)

    # need monotone x for interpolation; sort if necessary
    order = np.argsort(logf)
    logf, logs, logn = logf[order], logs[order], logn[order]

    logfa = np.log(_ANCHORS)
    logs_a = np.interp(logfa, logf, logs, left=logs[0], right=logs[-1])
    logn_a = np.interp(logfa, logf, logn, left=logn[0], right=logn[-1])

    ln_ratio = logs_a - logn_a

    keys = [
        "ln_ratio_FASave_over_FASnave_0.0018",
        "ln_ratio_FASave_over_FASnave_0.0056",
        "ln_ratio_FASave_over_FASnave_0.0178",
        "ln_ratio_FASave_over_FASnave_0.0562",
        "ln_ratio_FASave_over_FASnave_0.1778",
        "ln_ratio_FASave_over_FASnave_0.5623",
        "ln_ratio_FASave_over_FASnave_1.7783",
        "ln_ratio_FASave_over_FASnave_17.7828",
        "ln_ratio_FASave_over_FASnave_5.6234",
    ]
    return {k: float(v) for k, v in zip(keys, ln_ratio)}


# ---- convenience: arrays -> prediction ----
def predict_fc_from_arrays(freq, FAS_ave, FASn_ave):
    feats = compute_ln_ratio_features(freq, FAS_ave, FASn_ave)
    return score(feats)


# ---- convenience: features dict -> prediction ----
def predict_fc_from_features(features_dict):
    return score(features_dict)

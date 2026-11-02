import jmp_score as jmp
import numpy as np

def GRscore(indata):
    outdata = 0.674583239193396 + 0.0160954213464553 * indata[u"IM16"] + -0.000390116685379391 * indata[u"IM19"] + -0.00633802629652479 * indata[u"IM2"] + -0.000804093074247201 * indata[u"IM20"] + -0.22878812197341 * indata[u"IM21"] + 0.126596776607417 * indata[u"IM22"] + 0.0240357938910929 * indata[u"IM23"] + -0.11380158153933 * indata[u"IM24"] + 0.0000016099810129922 * indata[u"IM27"] + -0.000745099540802115 * indata[u"lnIM1"] + -0.024248896920341 * indata[u"lnIM10"] + -0.0089307441015174 * indata[u"lnIM14"] + 0.0129189818527765 * indata[u"lnIM15"] + 0.00651628774082576 * indata[u"lnIM18"] + -0.00463031793820182 * indata[u"lnIM26"] + 0.0176555266905796 * indata[u"lnIM28"] + -0.0315036275735795 * indata[u"lnIM29"] + 0.000459743907724529 * indata[u"lnIM3"] + -0.0920131568254285 * indata[u"lnIM30"] + -0.00392598945219656 * indata[u"lnIM4"] + 0.0500923672100507 * indata[u"lnIM6"] + -0.039382566528236 * indata[u"lnIM7"] + -0.0107416091113594 * indata[u"lnIM8"] + 0.0159667804404377 * indata[u"lnIM9"]
    return outdata

